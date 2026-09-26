"""策略框架 · ExecutionAdapter 执行适配器。

场内 XTP（可转债/场内基金（ETF/LOF/封基/REITs）/A 股股票，中泰 XTP 通道）/ 加密币安/OKX 两种实现。分项键 etf=场内基金全体。

实盘下单三级开关（AND）：
1. .env ENABLE_LIVE_TRADING（总闸，settings.is_live_trading_enabled）
2. Web live_trading_config 分项（risk.is_live_trading_allowed，在 check_order 前置检查）
   分项：convertible/etf/astock/binance_perp/okx_perp
3. strategy_config.enabled + backtest_verified（策略级，scheduler 层）
A 股股票走 XTPAdapter（中泰 XTP 能交易 A 股），受 astock 分项开关控制。
"""

from __future__ import annotations
import logging
import threading

logger = logging.getLogger(__name__)
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time

# 持仓稳定窗秒数（O-S1"键集连续两拍稳定"）：模块常量便于测试收窄（等待原语审计 P2-1）
POSITION_STABLE_WINDOW_S = 2.0


@dataclass
class Order:
    symbol: str
    action: str  # BUY / SELL
    volume: float = 0   # A股=int 股数；crypto=float（BTC 可下 0.001--盲审 A-1 根修，int 截断丢整单）
    price: float = 0.0
    order_type: str = "limit"  # limit / market
    client_id: str = ""


@dataclass
class Position:
    symbol: str
    volume: int
    avg_price: float
    pnl: float = 0.0
    direction: str = "direction_long"     # ST2（N-S3）：XTP 映射含 NET/LONG/SHORT（两融 Short 行）；D4 资源 id 化（direction_ 前缀防冲突）
    frozen: int = 0             # 冻结量；available = volume - frozen（T+1 可卖）
    yd_volume: int = 0          # 昨仓


class ExecutionAdapter(ABC):
    """执行适配器抽象。查询类默认空实现，子类按需 override。"""

    @abstractmethod
    def send_order(self, order: Order) -> str:
        """下单，返回 order_id（client_id）。"""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        """撤单。"""
        ...

    @abstractmethod
    def query_position(self) -> list[Position]:
        """查持仓。"""
        ...

    def query_account(self) -> list:
        """查资金（默认空，XTPAdapter 实现）。"""
        return []

    def query_orders(self) -> list:
        """查当日委托（默认空，从事件缓存读）。"""
        return []

    def query_trades(self) -> list:
        """查当日成交（默认空，从事件缓存读）。"""
        return []

    def get_vt_orderid(self, client_id: str) -> str | None:
        """client_id -> vnpy 委托号（成交/撤单关联用）。默认无实现返回 None。"""
        return None


# --- 场内 XTP 适配器（vnpy_xtp 网关，可转债/场内基金/A 股股票） ---

def _vnpy_exchange(ex: str):
    """项目交易所后缀 -> vnpy Exchange 枚举（延迟 import）。"""
    from vnpy.trader.constant import Exchange
    mapping = {"SHSE": Exchange.SSE, "SSE": Exchange.SSE, "SZSE": Exchange.SZSE}
    return mapping.get(ex.upper(), Exchange.SSE)


class XTPAdapter(ExecutionAdapter):
    """中泰 XTP 交易适配器（底层 vnpy_xtp.XtpGateway）。

    交易品种：可转债/场内基金/A 股股票（中泰 XTP 通道），受三级开关控制
    （astock/etf/convertible 分项）。vnpy 4.0 查询事件驱动：调 gateway.query_position/account()
    后异步推 EVENT_POSITION/ACCOUNT，本类注册监听收集到缓存，query_xxx 触发后轮询等结果。
    query_orders/trades 纯靠事件推送（XTP 网关无主动查委托/成交接口）。
    """

    def __init__(self, gateway=None, event_engine=None, order_prefix: str = ""):
        self._order_prefix = order_prefix  # R-BR10：多 worker 唯一性 {tid}:{epoch} 前缀
        self._gateway = gateway  # vnpy_xtp.XtpGateway 实例
        self._event_engine = event_engine or (gateway.event_engine if gateway else None)
        # 事件缓存
        self._orders: dict = {}      # vt_orderid -> OrderData
        self._trades: dict = {}      # vt_tradeid -> TradeData
        self._positions: dict = {}   # vt_positionid -> PositionData
        self._accounts: dict = {}    # accountid -> AccountData
        # client_id <-> vt_orderid 映射
        self._cid2vt: dict = {}
        self._vt2cid: dict = {}
        self._cid_seq = 0
        self._lock = threading.Lock()
        if self._event_engine:
            from vnpy.event import Event
            from vnpy.trader.event import EVENT_ORDER, EVENT_TRADE, EVENT_POSITION, EVENT_ACCOUNT
            self._event_engine.register(EVENT_ORDER, self._on_order)
            self._event_engine.register(EVENT_TRADE, self._on_trade)
            self._event_engine.register(EVENT_POSITION, self._on_position)
            self._event_engine.register(EVENT_ACCOUNT, self._on_account)

    # ── 事件回调（收集到缓存） ──

    def _on_order(self, event) -> None:
        d = event.data
        with self._lock:
            self._orders[d.vt_orderid] = d

    def _on_trade(self, event) -> None:
        d = event.data
        with self._lock:
            self._trades[d.vt_tradeid] = d

    def _on_position(self, event) -> None:
        d = event.data
        with self._lock:
            self._positions[d.vt_positionid] = d

    def _on_account(self, event) -> None:
        d = event.data
        with self._lock:
            self._accounts[d.accountid] = d

    # ── 工具 ──

    @staticmethod
    def parse_vt_symbol(vt_symbol: str) -> tuple[str, str]:
        """vt_symbol '603986.SHSE' -> ('603986', 'SHSE')。"""
        if "." not in vt_symbol:
            return vt_symbol, ""
        sym, ex = vt_symbol.rsplit(".", 1)
        return sym, ex.upper()

    # ── 下单/撤单 ──

    def send_order(self, order: Order) -> str:
        if self._gateway is None:
            return f"mock-{order.symbol}-{order.action}"
        from vnpy.trader.object import OrderRequest
        from vnpy.trader.constant import Direction, Offset, OrderType

        sym, ex = self.parse_vt_symbol(order.symbol)
        direction = Direction.LONG if order.action.upper() == "BUY" else Direction.SHORT
        otype = OrderType.MARKET if order.order_type == "market" else OrderType.LIMIT
        self._cid_seq += 1
        client_id = order.client_id or f"{self._order_prefix}c{self._cid_seq}"
        req = OrderRequest(
            symbol=sym,
            exchange=_vnpy_exchange(ex),
            direction=direction,
            type=otype,
            volume=order.volume,
            price=order.price,
            offset=Offset.NONE,
            reference=client_id,
        )
        vt_orderid = self._gateway.send_order(req)
        # F-27（2026-08-17）：vnpy_xtp 对不支持的交易所/类型返回 ""，TD 断线时 insertOrder
        # 返回 0 → vt_orderid 形如 "XTP.0"——两者都是"委托未真实发出"，必须识别为失败
        orderid_part = vt_orderid.rsplit(".", 1)[-1] if vt_orderid else ""
        if not vt_orderid or orderid_part == "0":
            logger.error("网关拒绝/未发出委托（vt_orderid=%r symbol=%s）", vt_orderid, order.symbol)
            return None
        with self._lock:
            self._cid2vt[client_id] = vt_orderid
            self._vt2cid[vt_orderid] = client_id
        return client_id

    def cancel_order(self, order_id: str) -> None:
        if self._gateway is None:
            return
        from vnpy.trader.object import CancelRequest
        with self._lock:
            vt_orderid = self._cid2vt.get(order_id, order_id)
            od = self._orders.get(vt_orderid)
        if od:
            # vnpy cancel 要纯 orderid（不含 gateway 前缀）+ symbol + exchange
            req = CancelRequest(orderid=od.orderid, symbol=od.symbol, exchange=od.exchange)
        else:
            # 退化：没缓存时无法还原 symbol/exchange。XTP cancel 实际只吃纯 orderid
            # （xtp_gateway.cancel_order 仅 cancelOrder(int(orderid), session_id)，symbol/exchange
            # 不参与）。F-39（2026-09-03）：原把整串 vt_orderid "XTP.123" 当 orderid → int() 崩溃，
            # parse_vt_symbol 又把其拆成 ("XTP","123") 错 symbol 错 exchange。改为剥前缀得纯
            # orderid；非纯数字（client_id 等）无法还原则放弃盲撤（盲撤错单比不撤更危险）。
            pure = order_id.rsplit(".", 1)[-1] if "." in order_id else order_id
            if not pure.isdigit():
                logger.error("撤单退化路径：%r 无纯数字 orderid，放弃撤单", order_id)
                return
            logger.warning("撤单退化路径：无缓存，用纯 orderid %s 盲撤（symbol 未知）", pure)
            req = CancelRequest(orderid=pure, symbol="", exchange=_vnpy_exchange(""))
        self._gateway.cancel_order(req)

    def get_vt_orderid(self, client_id: str) -> str | None:
        """client_id -> vnpy 委托号（send_order 写回 _cid2vt）。F-50：成交/撤单关联用。"""
        with self._lock:
            return self._cid2vt.get(client_id)

    # ── 查询（事件驱动，触发后轮询等结果） ──

    def query_position(self) -> list[Position]:
        if self._gateway is None:
            return []
        # ST2（N-S6）：查询前清缓存——只增不删的缓存会让清仓标的变幽灵仓（XTP 不再回报该行，
        # 旧值永存直到进程重启）。
        with self._lock:
            self._positions.clear()
        self._gateway.query_position()
        # O-S1：XTP 逐标的逐行推送（每标的一行），"首个键即退"会读到跨 poll tick 的子集
        # →部分批次入真相表。此处"键集连续两拍稳定"（200ms 无新行）才算收齐。
        # 超时可观测（P2-1，守则原则 2）：稳定窗耗尽时分级——有行未稳=部分快照告警；
        # 零行=空仓合法常态降 info 不刷屏（trading 60s 循环调用）。
        deadline = time.time() + POSITION_STABLE_WINDOW_S
        prev_keys = None
        keys = frozenset()   # 预置：窗口参数异常（≤0）时循环零拍也不致 NameError
        stable = False
        while time.time() < deadline:
            time.sleep(0.1)
            with self._lock:
                keys = frozenset(self._positions.keys())
            if keys and keys == prev_keys:
                stable = True
                break
            prev_keys = keys
        if not stable:
            if keys:
                logger.warning("持仓稳定窗 %.1fs 未达成（现 %d 行）——返回部分快照，对账注意",
                               POSITION_STABLE_WINDOW_S, len(keys))
            else:
                logger.info("持仓查询 %.1fs 零回报——空仓或查询无响应", POSITION_STABLE_WINDOW_S)
        with self._lock:
            return [
                Position(
                    symbol=p.vt_symbol,
                    volume=int(p.volume),
                    avg_price=float(p.price),
                    pnl=float(getattr(p, "pnl", 0.0) or 0.0),
                    direction="direction_" + getattr(p.direction, "name", "LONG").lower(),
                    frozen=int(getattr(p, "frozen", 0) or 0),
                    yd_volume=int(getattr(p, "yd_volume", 0) or 0),
                )
                for p in self._positions.values()
            ]

    def query_account(self) -> list:
        if self._gateway is None:
            return []
        # F-40（2026-09-03）：查询前清缓存（同 query_position 的 ST2 修）——断线时 gateway
        # 静默 no-op 无新 EVENT_ACCOUNT，旧缓存会让 snapshot_cycle 的 `if not accounts` 守卫
        # 失效、account_snapshot 被陈旧值污染。清缓存后断线返回空 → snapshot_cycle 正确跳过。
        with self._lock:
            self._accounts.clear()
            before = set(self._accounts.keys())
        self._gateway.query_account()
        self._wait_update(self._accounts, before, timeout=2.0)
        with self._lock:
            return list(self._accounts.values())

    def query_orders(self) -> list:
        with self._lock:
            return list(self._orders.values())

    def query_trades(self) -> list:
        with self._lock:
            return list(self._trades.values())

    @staticmethod
    def _wait_update(cache: dict, before: set, timeout: float = 2.0) -> bool:
        """轮询等事件推送更新缓存（vnpy 查询是异步）。

        返回 False=超时且已告警（P2-1 超时可观测：缓存内容可能是部分/旧值）。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if set(cache.keys()) != before:
                return True
            time.sleep(0.1)
        logger.warning("查询更新等待超时（%.1fs）——缓存 %d 项可能是部分/旧值", timeout, len(cache))
        return False


# --- 加密适配器基类 ---

class CryptoPerpAdapter(ExecutionAdapter):
    """加密永续合约适配器基类（币安/OKX）。"""

    def __init__(self, gateway=None):
        self._gateway = gateway
        self._leverage = 1
        self._margin_mode = "isolated"

    def set_leverage(self, leverage: int):
        self._leverage = max(1, min(leverage, 5))  # 上限 5x

    def send_order(self, order: Order) -> str:
        if self._gateway is None:
            return f"mock-crypto-{order.symbol}-{order.action}"
        return f"crypto-{order.symbol}-{id(order)}"

    def cancel_order(self, order_id: str) -> None:
        pass

    def query_position(self) -> list[Position]:
        return []


def _load_emt_binding():
    """惰性加载 EMT 交易绑定（本地无 .so/CI 环境下 adapters.py 仍可 import——EmqMdGateway 先例）。"""
    import importlib
    import os as _os
    import sys as _sys
    here = _os.path.join(_os.path.dirname(__file__), "emd")
    if here not in _sys.path:
        _sys.path.insert(0, here)
    return importlib.import_module("emt_trader_api")


_EX_TO_EMT_MARKET = {"SHSE": "SH_A", "SZSE": "SZ_A", "BSE": "BJ_A"}


class EmtAdapter(ExecutionAdapter):
    """东财 EMT 极速柜台适配器（批 63 P4——TD_BUILDERS 注册表第二实例）。

    **vnpy 形状契约（批 63 P4 规格 A/B 双盲同判 P0 立法）**：worker 消费链
    （reconcile_orders/halt_edge_cancel/write_trade_log/snapshot_cycle）全部假定 vnpy
    OrderData/TradeData/AccountData 形状+事件引擎——本类在 SPI 回调内合成 vnpy 对象经
    event_engine 推 EVENT_ORDER/TRADE/POSITION/ACCOUNT，并维护 _vt2cid/_lock 同形属性
    +override get_vt_orderid。vt_orderid="EMT.{order_emt_id}"。

    EMT 特有：session_id 生命周期（重登序列 Logout→SubscribePublicTopic(RESTART)→Login）；
    查询异步分帧（request_id+is_last 收齐）；order_client_id 是 uint32（字符串 client_id
    自派生短号+双向映射表）；SDK 单例（CreateTraderApi 重复调用返同句柄——模块级防御）。
    """

    _API_SINGLETON = None   # SDK 单例防御（builder/测试同进程二次 Create 撞句柄）

    def __init__(self, gateway=None, event_engine=None, order_prefix: str = "",
                 td_client_id: int = 20):
        # gateway 兼容位（None→mock 形态零参构造，stub 分支/测试用）；真 API 经 connect() 建
        self._gateway = gateway
        self._event_engine = event_engine
        self._order_prefix = order_prefix
        self._td_client_id = int(td_client_id)
        self._api = None
        self._spi = None
        self._session_id = 0
        self._trading_day = ""
        # vnpy 同形缓存（XTPAdapter 模式）
        self._orders: dict = {}
        self._trades: dict = {}
        self._positions: dict = {}
        self._accounts: dict = {}
        self._cid2vt: dict = {}
        self._vt2cid: dict = {}
        self._cid2sn: dict = {}     # client_id 字符串 -> uint32 短号（order_client_id 宽度）
        self._sn_seq = 0
        self._cid_seq = 0
        self._request_seq = 0
        self._pending: dict = {}    # request_id -> {"frames": [], "done": bool}
        self._lock = threading.Lock()

    # ── 连接（builder 的 gw.connect(setting) 调此） ──

    @property
    def connect_status(self) -> bool:
        """hub_worker 消费（getattr(td_api,"connect_status",True) 默认 True=不挡暖机）。"""
        return self._session_id != 0

    def connect(self, setting: dict) -> None:
        """建连：单例防御→CreateTraderApi→RegisterSpi→Subscribe(RESTART)→Login。

        setting: {td_host, td_port, account, password}（builder 从接口行组装）。
        Login 返回 0=失败 raise（fail-fast——worker exit 78 语义由 main 调用点承接）。
        """
        mod = _load_emt_binding()
        old_session = self._session_id          # 重连态：SDK :719 先 Logout 再 Login 公共流才生效
        with self._lock:
            if EmtAdapter._API_SINGLETON is not None and EmtAdapter._API_SINGLETON is not self._api:
                logger.warning("EMT TraderApi 单例已存在（他处 Create）——复用同句柄")
                self._api = EmtAdapter._API_SINGLETON
            if self._api is None:
                import os as _os2
                self._api = mod.TraderApi.CreateTraderApi(
                    self._td_client_id, _os2.path.expanduser("~/.quant/emt_td_logs"))
                if self._api is None:
                    raise RuntimeError(
                        f"EMT CreateTraderApi 返 None（client_id={self._td_client_id} 越界 [1-127]?）")
                EmtAdapter._API_SINGLETON = self._api
            if self._spi is None:
                self._spi = self._make_spi(mod)
                self._api.RegisterSpi(self._spi)   # keep_alive：self._spi 自持防 GC
        if old_session:
            try:
                self._api.Logout(old_session)
            except Exception:
                pass
        self._api.SubscribePublicTopic(mod.EMT_TE_RESUME_TYPE.RESTART)   # 当日全量重传（N1 钉死）
        sid = self._api.Login(setting["td_host"], int(setting["td_port"]),
                              setting["account"], setting["password"], 1)   # 1=TCP
        if not sid:
            err = self._api.GetApiLastError()
            raise RuntimeError(f"EMT Login 失败（session=0）：{getattr(err, 'error_msg', '?')}"
                               f" code={getattr(err, 'error_id', '?')}")
        self.remember_login(setting)
        with self._lock:
            self._session_id = sid
            self._trading_day = self._api.GetTradingDay() or ""
        self._log("connected", f"session={sid} trading_day={self._trading_day}")

    def _relogin(self) -> None:
        """重登序列（SDK :719：断线后不 Logout 直接 Login 公共流不生效）。"""
        old = self._session_id
        mod = _load_emt_binding()
        if old:
            self._api.Logout(old)
        self._api.SubscribePublicTopic(mod.EMT_TE_RESUME_TYPE.RESTART)
        st = getattr(self, "_last_setting", None)
        sid = 0
        if st:
            sid = self._api.Login(st["td_host"], int(st["td_port"]),
                                  st["account"], st["password"], 1)
        if not sid:
            raise RuntimeError("EMT 重登失败（session=0）")
        with self._lock:
            self._session_id = sid
        self._log("relogin", f"old={old} new={sid}")

    parse_vt_symbol = staticmethod(XTPAdapter.parse_vt_symbol)   # 复用 XTP 的 vt 解析

    def _ensure_session(self) -> int:
        sid = self._session_id
        if not sid:
            raise RuntimeError("EMT session 失效（未登录/断线）——fail-fast 拒绝调用")
        return sid

    def _log(self, tag: str, body: str) -> None:
        """[gw] TD 会话日志通道（批 6b 语义对齐——无 vnpy 网关故 logger 直写）。"""
        logger.info("[gw] EMT %s: %s", tag, body)

    # ── SPI（合成 vnpy 对象推事件） ──

    def _make_spi(self, mod):
        adapter = self

        class _Spi(mod.TraderSpi):
            def OnConnected(self):
                adapter._log("connected_evt", "")

            def OnDisconnected(self, reason):
                # 代码审 A-P1-1/B-P0-3 同判接线：置零 session——connect_status 翻 False，
                # hub_worker._td_reconnect 建连腿接管（60s 节流）；重登语义由 connect() 承接
                # （其内先 Logout 旧 session 再 Subscribe→Login——SDK :719 公共流生效次序）
                adapter._log("disconnected", f"reason={reason}（session 置零，建连腿接管）")
                with adapter._lock:
                    adapter._session_id = 0

            def OnError(self, err):
                adapter._log("error", f"{getattr(err, 'error_msg', '?')} ({getattr(err, 'error_id', '?')})")

            def OnOrderEvent(self, o, err, session):
                adapter._on_order_raw(o, err, session)

            def OnTradeEvent(self, t, session):
                adapter._on_trade_raw(t, session)

            def OnCancelOrderError(self, c, err, session):
                adapter._log("cancel_error",
                             f"order_emt_id={getattr(c, 'order_emt_id', '?')} "
                             f"{getattr(err, 'error_msg', '?')}")

            def OnQueryOrder(self, o, err, request_id, is_last, session):
                adapter._frame(request_id, o, is_last)

            def OnQueryPosition(self, p, err, request_id, is_last, session):
                adapter._frame(request_id, p, is_last)

            def OnQueryAsset(self, a, err, request_id, is_last, session):
                adapter._frame(request_id, a, is_last)

        return _Spi()

    # EMT 原生 -> vnpy 对象合成

    @staticmethod
    def _status_of(raw_status):
        """8 值枚举映射（部撤=终态归 CANCELLED——reconcile 在场判断不含部撤；UNKNOWN 保守
        SUBMITTING=在场方向，漏撤比误撤安全）。"""
        from vnpy.trader.constant import Status
        mod = _load_emt_binding()
        m = {
            mod.EMT_ORDER_STATUS_TYPE.INIT: Status.SUBMITTING,
            mod.EMT_ORDER_STATUS_TYPE.ALLTRADED: Status.ALLTRADED,
            mod.EMT_ORDER_STATUS_TYPE.PARTTRADEDQUEUEING: Status.PARTTRADED,
            mod.EMT_ORDER_STATUS_TYPE.NOTRADEQUEUEING: Status.NOTTRADED,
            mod.EMT_ORDER_STATUS_TYPE.PARTTRADEDNOTQUEUEING: Status.CANCELLED,   # 部撤终态
            mod.EMT_ORDER_STATUS_TYPE.CANCELED: Status.CANCELLED,
            mod.EMT_ORDER_STATUS_TYPE.REJECTED: Status.REJECTED,
            mod.EMT_ORDER_STATUS_TYPE.UNKNOWN: Status.SUBMITTING,
        }
        return m.get(raw_status, Status.SUBMITTING)

    def _to_order_data(self, o: dict):
        """o=物化 dict（事件/查询双面统一）。"""
        from vnpy.trader.object import OrderData
        from vnpy.trader.constant import Direction, Offset, OrderType
        sym, ex = self.parse_vt_symbol((o.get("ticker") or "") + self._ex_suffix(o.get("market")))
        vt = f"EMT.{o['order_emt_id']}"
        pt = o.get("price_type")
        pt = pt.value if hasattr(pt, "value") else (pt or 0)
        sd = o.get("side") or 0
        od = OrderData(
            gateway_name="EMT", symbol=sym, exchange=_vnpy_exchange(ex),
            orderid=str(o["order_emt_id"]),
            type=OrderType.LIMIT if int(pt) == 1 else OrderType.MARKET,
            direction=Direction.LONG if int(sd) == 1 else Direction.SHORT,
            offset=Offset.NONE, price=o.get("price") or 0.0, volume=float(o.get("quantity") or 0),
            traded=float(o.get("qty_traded") or 0), status=self._status_of(o.get("order_status")),
            reference=str(o.get("order_client_id") or 0))
        od.vt_orderid = vt
        # client_id 还原：短号反查字符串（回报 order_client_id=短号）
        with self._lock:
            for cid, sn in self._cid2sn.items():
                if sn == o.get("order_client_id"):
                    od.reference = cid
                    break
        return od

    @staticmethod
    def _emt_time(t_raw) -> object:
        """int64 YYYYMMDDHHMMSSsss → tz-aware datetime（md_gateway 先例；坏值返 None）。"""
        try:
            from datetime import datetime as _dt
            v = int(t_raw or 0)
            if v <= 0:
                return None
            s = str(v).ljust(17, "0")[:14]
            from zoneinfo import ZoneInfo
            return _dt.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        except Exception:
            return None

    def _to_trade_data(self, t: dict):
        """t=物化 dict。tradeid=exec_id（SDK 逐笔唯一键），空则 report_index 兜底。"""
        from vnpy.trader.object import TradeData
        from vnpy.trader.constant import Direction, Offset
        sym, ex = self.parse_vt_symbol((t.get("ticker") or "") + self._ex_suffix(t.get("market")))
        tradeid = t.get("exec_id") or str(t.get("report_index") or 0)
        sd = t.get("side") or 0
        td = TradeData(
            gateway_name="EMT", symbol=sym, exchange=_vnpy_exchange(ex),
            orderid=str(t["order_emt_id"]), tradeid=str(tradeid),
            direction=Direction.LONG if int(sd) == 1 else Direction.SHORT,
            offset=Offset.NONE, price=t.get("price") or 0.0, volume=float(t.get("quantity") or 0),
            datetime=self._emt_time(t.get("trade_time")))
        td.vt_orderid = f"EMT.{t['order_emt_id']}"
        return td

    @staticmethod
    def _ex_suffix(market) -> str:
        """交易 market 枚举（1=SZ/2=SH 对调）→ vt 后缀。.value 兼容 pybind11/纯 Python 枚举。"""
        mod = _load_emt_binding()
        inv = {mod.EMT_MARKET_TYPE.SH_A.value: ".SHSE", mod.EMT_MARKET_TYPE.SZ_A.value: ".SZSE",
               mod.EMT_MARKET_TYPE.BJ_A.value: ".BSE"}
        mv = market.value if hasattr(market, "value") else (market or 0)
        return inv.get(int(mv), ".SHSE")

    def _on_order_raw(self, o, err, session) -> None:
        od = self._to_order_data(self._snapshot(o))
        with self._lock:
            self._orders[od.vt_orderid] = od
            if err is not None and getattr(err, "error_id", 0):
                self._log("order_error", f"{od.vt_orderid} {err.error_msg}")
        self._emit("eEVENT_ORDER", od)

    def _on_trade_raw(self, t, session) -> None:
        td = self._to_trade_data(self._snapshot(t))
        with self._lock:
            self._trades[f"{td.vt_orderid}.{td.tradeid}"] = td
        self._emit("eEVENT_TRADE", td)

    def _emit(self, ev_alias: str, data) -> None:
        if not self._event_engine:
            return
        from vnpy.event import Event
        from vnpy.trader import event as vevent
        etype = {"eEVENT_ORDER": vevent.EVENT_ORDER, "eEVENT_TRADE": vevent.EVENT_TRADE}.get(ev_alias)
        if etype:
            self._event_engine.put(Event(type=etype, data=data))

    # ── 分帧查询聚合 ──

    _SNAP_ATTRS = ("order_emt_id", "order_client_id", "ticker", "market", "price",
                   "quantity", "price_type", "side", "qty_traded", "qty_left",
                   "order_status", "trade_time", "trade_amount", "report_index",
                   "exec_id", "order_local_id", "order_exch_id",
                   "total_qty", "sellable_qty", "avg_price", "unrealized_pnl",
                   "yesterday_position", "total_asset", "buying_power", "security_asset")

    def _snapshot(self, rsp) -> dict:
        """帧内物化（代码审 A/B 同判 P0-2：SDK 回调指针仅帧内有效——帧外读=UAF/别名）。"""
        if isinstance(rsp, dict):
            return rsp
        return {a: getattr(rsp, a, None) for a in self._SNAP_ATTRS}

    def _frame(self, request_id: int, rsp, is_last: bool) -> None:
        """查询分帧：回调线程里即时物化为纯 Python dict。"""
        snap = self._snapshot(rsp)
        with self._lock:
            slot = self._pending.setdefault(int(request_id), {"frames": [], "done": False})
            slot["frames"].append(snap)
            if is_last:
                slot["done"] = True

    def _next_request_id(self) -> int:
        with self._lock:
            self._request_seq += 1
            return self._request_seq

    def _collect(self, request_id: int, timeout: float = 3.0) -> list:
        """轮询等分帧收齐（is_last；超时返已收帧——partial 可观测非静默）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                slot = self._pending.get(request_id)
                if slot and slot["done"]:
                    frames = slot["frames"]
                    self._pending.pop(request_id, None)
                    return frames
            time.sleep(0.05)
        with self._lock:
            slot = self._pending.pop(request_id, None)
        frames = slot["frames"] if slot else []
        logger.warning("EMT 查询 %s 超时收 %d 帧（is_last 未到，返已收帧）", request_id, len(frames))
        return frames

    def _query_rc_error(self, name: str, rc: int) -> None:
        err = self._api.GetApiLastError() if self._api else None
        logger.warning("EMT %s 提交失败 rc=%s：%s", name, rc,
                       getattr(err, "error_msg", "?"))

    # ── ExecutionAdapter 契约 ──

    def send_order(self, order: Order) -> str:
        if self._api is None:
            return f"mock-emt-{order.symbol}-{order.action}"
        mod = _load_emt_binding()
        sid = self._ensure_session()
        sym, ex = self.parse_vt_symbol(order.symbol)
        self._cid_seq += 1
        client_id = order.client_id or f"{self._order_prefix}c{self._cid_seq}"
        with self._lock:
            self._sn_seq = (self._sn_seq % 0xFFFFFF) + 1   # uint32 域避开 0
            sn = self._sn_seq
            self._cid2sn[client_id] = sn
        req = mod.EMTOrderInsertInfo()
        req.ticker = sym
        req.market = getattr(mod.EMT_MARKET_TYPE, _EX_TO_EMT_MARKET.get(ex, "SH_A"))
        req.price = float(order.price or 0.0)
        req.quantity = int(order.volume)
        req.price_type = (mod.EMT_PRICE_TYPE.BEST5_OR_CANCEL if order.order_type == "market"
                          else mod.EMT_PRICE_TYPE.LIMIT)   # 市价钉五档剩撤（沪深通用）
        req.side = mod.EMT_SIDE_BUY if order.action.upper() == "BUY" else mod.EMT_SIDE_SELL
        req.order_client_id = sn
        req.business_type = mod.EMT_BUSINESS_TYPE_CASH
        emt_id = self._api.InsertOrder(req, sid)
        if not emt_id:
            err = self._api.GetApiLastError()
            logger.error("EMT 委托未发出（emt_id=0 symbol=%s）：%s", order.symbol,
                         getattr(err, "error_msg", "?"))
            return None
        vt = f"EMT.{emt_id}"
        with self._lock:
            self._cid2vt[client_id] = vt
            self._vt2cid[vt] = client_id
        return client_id

    def cancel_order(self, order_id: str) -> None:
        if self._api is None:
            return
        sid = self._ensure_session()
        with self._lock:
            vt = self._cid2vt.get(order_id, order_id)
        pure = vt.rsplit(".", 1)[-1]
        if not pure.isdigit():
            logger.error("EMT 撤单退化路径：%r 无纯数字 order_emt_id，放弃", order_id)
            return
        if not self._api.CancelOrder(int(pure), sid):
            err = self._api.GetApiLastError()
            logger.warning("EMT 撤单发送失败 %s：%s", pure, getattr(err, "error_msg", "?"))

    def get_vt_orderid(self, client_id: str) -> str | None:
        with self._lock:
            return self._cid2vt.get(client_id)

    def query_position(self) -> list[Position]:
        if self._api is None:
            return []
        self._check_eod()                      # 先 EOD（代码审 B-P1-5：重建后重取 sid）
        sid = self._ensure_session()
        rid = self._next_request_id()
        rc = self._api.QueryPosition(None, sid, rid)
        if rc:
            self._query_rc_error("QueryPosition", rc)
        frames = self._collect(rid)
        out = []
        for p in frames:   # p=物化 dict
            sym = (p.get("ticker") or "") + self._ex_suffix(p.get("market"))
            out.append(Position(symbol=sym, volume=int(p.get("total_qty") or 0),
                                avg_price=float(p.get("avg_price") or 0.0),
                                pnl=float(p.get("unrealized_pnl") or 0.0),
                                direction="direction_long",
                                frozen=int(p.get("total_qty") or 0) - int(p.get("sellable_qty") or 0),
                                yd_volume=int(p.get("yesterday_position") or 0)))
        return out

    def query_account(self) -> list:
        if self._api is None:
            return []
        self._check_eod()                      # 先 EOD（重建后重取 sid）
        sid = self._ensure_session()
        rid = self._next_request_id()
        rc = self._api.QueryAsset(sid, rid)
        if rc:
            self._query_rc_error("QueryAsset", rc)
        frames = self._collect(rid)
        # vnpy AccountData 形状合成（snapshot_cycle 消费 balance/frozen——A-P0 立法）
        out = []
        for a in frames:   # a=物化 dict
            from vnpy.trader.object import AccountData
            ad = AccountData(gateway_name="EMT", accountid="EMT",
                             balance=float(a.get("total_asset") or 0.0),
                             frozen=float(a.get("total_asset") or 0.0) - float(a.get("buying_power") or 0.0))
            ad.available = float(a.get("buying_power") or 0.0)
            out.append(ad)
        return out

    def query_orders(self) -> list:
        """缓存读（XTP 模式）；在场单快照补 QueryUnfinishedOrders（reconcile 消费面）。"""
        if self._api is not None and self._session_id:
            sid = self._ensure_session()
            rid = self._next_request_id()
            rc = self._api.QueryUnfinishedOrders(sid, rid)
            if rc:
                self._query_rc_error("QueryUnfinishedOrders", rc)
            for o in self._collect(rid, timeout=2.0):
                self._on_order_raw(o, None, sid)
        with self._lock:
            return list(self._orders.values())

    def query_trades(self) -> list:
        """缓存读（成交补录靠公共流 RESTART 重传——规格 N1 立法，不绑 QueryTrades）。"""
        with self._lock:
            return list(self._trades.values())

    def _check_eod(self) -> None:
        """EOD 检测：交易日变更（SDK 不支持过夜）——重建序列 清单例→Release→connect。

        worker 跨夜存活是 XTP 窗设计前提（不能靠单元重启）——A-P1-3 修法。
        """
        try:
            day = self._api.GetTradingDay() or ""
        except Exception:
            return
        if not (day and day.isdigit() and day != "0"):   # 未登录态返 '0'（B-P2-5 防重建风暴）
            return
        if self._trading_day and day != self._trading_day:
            logger.warning("EMT 交易日变更 %s→%s：Release 重建（SDK 不支持过夜）",
                           self._trading_day, day)
            with self._lock:
                EmtAdapter._API_SINGLETON = None
                self._session_id = 0
                old_api, self._api, self._spi = self._api, None, None
                self._orders.clear(); self._trades.clear()
                self._positions.clear(); self._accounts.clear()
                self._cid2vt.clear(); self._vt2cid.clear(); self._cid2sn.clear()
            try:
                old_api.Release()   # 单例句柄必须显式释放（否则 Create 返同句柄=重建失效）
            except Exception:
                pass
            # 重建（上次登录参数缓存）——失败 raise 由调用面接
            self.connect(self._last_setting)
            self._trading_day = day

    # builder 组装用：登录参数缓存（重登/EOD 重建复用）
    def remember_login(self, setting: dict) -> None:
        """登录参数缓存（EOD 重建 connect(setting) 复用）。"""
        self._last_setting = dict(setting)

    def _last_login_args_pack(self):
        return self._last_setting


# --- 适配器工厂 ---

def create_adapter(adapter_type: str, gateway=None, event_engine=None) -> ExecutionAdapter:
    """创建适配器实例。XTPAdapter 可传 event_engine 注册事件监听。
    A 股股票/可转债/场内基金统一走 'xtp'（中泰 XTP 通道）。
    """
    mapping = {
        "xtp": XTPAdapter,
        "emt_emq": EmtAdapter,      # 批 63 P4：TD 注册表第二实例
        "binance_perp": CryptoPerpAdapter,
        "okx_perp": CryptoPerpAdapter,
    }
    cls = mapping.get(adapter_type)
    if cls is None:
        raise ValueError(f"未知适配器类型: {adapter_type}，可选: {list(mapping.keys())}")
    if cls in (XTPAdapter, EmtAdapter):
        return cls(gateway=gateway, event_engine=event_engine)
    return cls(gateway=gateway)
