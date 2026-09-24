"""行情网关插件层（批 63 二）——hub MD 接入从 XTP 硬编码改为按 provider 插件化。

分工（批 63 方案裁定）：
- 留 hub（provider 无关）：MinuteAggregator 聚合 / _publish 分发 / 租约·gen·心跳（M5 四键）/
  _desired_symbols 订阅真相源 / SubscriptionManager diff-replay / flush 三窗。
- 进插件（provider 特有）：连接原语 / 订阅退订原语 / tick 喂入 / 重连沿 / 会话窗与监督 / SDK 守卫。

tick 契约（EMQ 等自研网关 Phase B 须产出兼容对象，duck typing）：
1. 字段面：last_price/datetime/volume/turnover/exchange/symbol + open/high/low/pre_close/
   limit_up/limit_down + bid_price_1..5/ask_price_1..5/bid_volume_1..5/ask_volume_1..5
   （_write_latest_tick 与 MinuteAggregator 消费面；vnpy TickData 原生满足）。
2. datetime 必须 tz-aware Asia/Shanghai（中国时刻）——_in_bar_session 用 t.hour/min 对中国时段
   判界、MinuteAggregator 按 t.date 翻日；naive 会崩，UTC 会整段错位。XTP 网关原样
   strptime(data_time).replace(tzinfo=CHINA_TZ)（批 56b 的 UTC 立法只作用于 bar 落库 ts，非 tick）。
3. exchange 须 duck-type 到 vnpy 枚举：.value 取 "SSE"/"SZSE"（非 SHSE——parts._project_symbol
   做 SSE→SHSE 项目后缀映射，EMQ 实现者照直觉给 SHSE 会产出 "XXX.SHSE.SHSE" 错键）。
4. turnover 缺失时 parts 侧 getattr(tick,"turnover",0) 静默降级——EMQ 的 amount 须显式映射到
   turnover，否则 bar.amount 恒 0 无告警。

全部 vnpy/XTP import 惰性（模块可被无 vnpy 环境导入——测试/层序）。
"""
from __future__ import annotations
import logging
import os
import queue
import threading
from abc import ABC, abstractmethod
from datetime import datetime

# 观测面等值（盲审 A P3-5）：[gw] 会话日志/退订日志的 logger 名保持 md_hub——
# journalctl 按名过滤与 system_log 落库 name 字段均不因插件化漂移
logger = logging.getLogger("md_hub")

try:
    from vnpy.trader.gateway import BaseGateway
except ImportError:   # 无 vnpy 环境可导入本模块（测试/层序；hub 启动时统一报错退出）
    BaseGateway = object

_REGISTRY: dict[str, type] = {}


class ThinGateway(BaseGateway):
    """仅事件转发；7 个抽象方法全量 stub（hub 数据面永不交易，R-HALT1 代码级保证）。

    批 63 二从 md_hub.parts 移驻本模块（层序：strategy_framework 层 2 禁 import 层 3；
    ThinGateway 本质=vnpy 网关薄壳，归 vnpy 集成层）。
    """

    def connect(self, setting: dict) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def subscribe(self, req) -> None:
        self.md_api.subscribe(req)

    def send_order(self, req) -> str:
        raise NotImplementedError("hub 数据面禁用")

    def cancel_order(self, req) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def query_account(self) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def query_position(self) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def close(self) -> None:
        pass


class MdGateway(ABC):
    """行情网关插件抽象。每供应商一个子类，构造收 hub 的 SessionCounters（监督沿清零共享）。"""

    provider: str

    event_engines: tuple = ()   # 事件引擎存活面（EngineLoop R-BR12 检查用；无事件引擎的网关空元组）

    @abstractmethod
    def connect(self, cred: dict, params: dict) -> None:
        """建连（凭证/参数来自 external_interface 行解密；会话窗策略由实现内部管理）。"""

    @abstractmethod
    def subscribe(self, symbol: str) -> None:
        """订阅原语（vt_symbol，如 600000.SHSE）。"""

    @abstractmethod
    def unsubscribe(self, symbol: str) -> None:
        """退订原语（桶 flush 由 hub 侧做——「先 flush 后退订」顺序在 hub 的 unsubscribe 回调里）。"""

    @abstractmethod
    def set_on_tick(self, cb) -> None:
        """注册 tick 回调 cb(tick)（契约见模块 docstring）。"""

    @property
    @abstractmethod
    def connected(self) -> bool:
        """连接在位（重连沿——False→True 沿触发 hub 订阅全量重放）。"""

    @abstractmethod
    def start_ready(self) -> bool:
        """启动时是否可立即回放订阅（XTP=会话窗开；其他源自定策略）。"""

    @abstractmethod
    def poll_supervise(self, in_session: bool, trading_day) -> None:
        """会话监督 tick（主循环每步驱动；XTP=续航/反应式重登/断流告警五段）。"""

    def set_context(self, fn) -> None:
        """告警上下文（hub 设订阅数文案；默认 no-op）。"""

    # shutdown 无此成员——hub 退出走 os._exit 带码自灭（原生库拆除规避是刻意设计，
    # 盲审 P3：不留永不调用的死接口）


def register_md_gateway(cls):
    _REGISTRY[cls.provider] = cls
    return cls


def create_md_gateway(provider: str, counters) -> MdGateway:
    """按接口行 provider 取网关插件（未注册 fail-fast——消费方须捕获转 exit 78）。"""
    cls = _REGISTRY.get(provider)
    if cls is None:
        raise ValueError(f"未注册的行情网关 provider: {provider}（需实现 MdGateway 子类并 register_md_gateway）")
    return cls(counters)


def list_md_gateway_providers() -> tuple[str, ...]:
    """已注册网关 provider 集（get_interface_row 缺省选行钉定用——盲审 P1）。"""
    return tuple(sorted(_REGISTRY))


@register_md_gateway
class XtpMdGateway(MdGateway):
    """中泰 XTP 网关（批 63 二收编——行为等值包装原 hub main.py MD 接入段全套，不重写）。

    vnpy EventEngine + ThinGateway + GuardedXtpMdApi（SDK 守卫）+ XtpMdSession（每日连接窗）+
    MdSessionSupervisor（L2 五段）+ 订阅映射（_EX/EXCHANGE_VT2XTP）。
    """

    provider = "xtp"

    def __init__(self, counters):
        from vnpy.event import EventEngine
        from vnpy.trader.event import EVENT_LOG
        from vnpy.trader.constant import Exchange
        from src.strategy_framework.md_api_guard import GuardedXtpMdApi
        from src.strategy_framework.md_session import XtpMdSession, load_xtp_window_cfg
        from src.strategy_framework.runtime.alerts import make_alert
        from src.strategy_framework.runtime.mdlink import MdSessionSupervisor

        self._ee = EventEngine()
        self._ee.start()   # 绕开 MainEngine 必须自启（构造不启动，_active=False → 线程未活）
        self._gw = ThinGateway(self._ee, "XTP")
        self._api = GuardedXtpMdApi(self._gw)   # 批1：SDK 生命周期守卫（SEGV 结构性绝迹）
        self._gw.md_api = self._api
        # L2 会话（韧性分层）+每日连接窗（P2 批 08-28）：lead/lag>0 窗开沿=续航单原语建连（relogin 直登）、
        # 窗关沿=guard.suspend（logout 保持 CREATED）；任一 0=禁用（旧行为）
        _lead, _lag = load_xtp_window_cfg()
        self._sess = XtpMdSession(self._api, lead_min=_lead, lag_min=_lag)
        self._context_fn = lambda: ""
        self._sup = MdSessionSupervisor(self._sess, counters, make_alert(), role="hub",
                                        context=lambda: self._context_fn())
        self._EX = {"SHSE": Exchange.SSE, "SZSE": Exchange.SZSE,
                    "BSE": getattr(Exchange, "BSE", Exchange.SSE)}
        self.event_engines = (self._ee,)   # EngineLoop 事件线程存活检查面（R-BR12 原语义）

        # MD 生命周期可见化（2026-08-24 僵尸会话事件）：连接/断开/重登日志走 EVENT_LOG（原 hub on_log 段
        # 原样；guard 保留——vnpy 事件线程对 handler 异常不捕获，裸抛=线程死 → EngineLoop fatal exit 1）
        from src.strategy_framework.runtime.alerts import make_alert, make_guard

        @make_guard("hub.on_log", make_alert())
        def _on_log(event):
            logger.info("[gw] %s", getattr(event.data, "msg", event.data))
        self._ee.register(EVENT_LOG, _on_log)

    def set_on_tick(self, cb) -> None:
        from vnpy.trader.event import EVENT_TICK
        self._ee.register(EVENT_TICK, lambda event: cb(event.data))

    def connect(self, cred: dict, params: dict) -> None:
        from src.strategy_framework.broker import xtp_setting_from
        setting = xtp_setting_from(cred, params)
        self._api.connect(setting["账号"], setting["密码"], int(setting["客户号"]),
                          setting["行情地址"], int(setting["行情端口"]), setting.get("行情协议", "TCP"), 3,
                          defer_login=not self._sess.window_open())   # 窗关启动只建 C 对象（CREATED），窗开沿 relogin 直登

    def subscribe(self, symbol: str) -> None:
        from vnpy.trader.object import SubscribeRequest
        raw, ex = symbol.rsplit(".", 1)
        e = self._EX.get(ex)
        if e:
            self._api.subscribe(SubscribeRequest(symbol=raw, exchange=e))

    def unsubscribe(self, symbol: str) -> None:
        """SDK 原生退订（原 hub _unsubscribe 的 SDK 段原样）。

        EXCHANGE_VT2XTP 无 BSE 键——.get() 取 None 静默跳过（与 subscribe 对称）；
        非 LOGGED_IN 态裸调 C 面有炸回调线程风险，跳过+debug（真实兜底=重连后全量重放）。
        """
        from vnpy_xtp.gateway.xtp_gateway import EXCHANGE_VT2XTP
        from src.strategy_framework.md_api_guard import SdkState
        raw, ex = symbol.rsplit(".", 1)
        e = self._EX.get(ex)
        xtp_ex = EXCHANGE_VT2XTP.get(e) if e else None
        if xtp_ex is None:
            return
        if self._api.state is not SdkState.LOGGED_IN:
            logger.debug("退订跳过 %s（MD 态 %s 非登录态，待重连后 diff 补齐）",
                         symbol, self._api.state.value)
            return
        self._api.unSubscribeMarketData(raw, 1, xtp_ex)
        logger.info("退订 %s（生命周期结束）", symbol)

    @property
    def connected(self) -> bool:
        return bool(getattr(self._api, "connect_status", True))

    def start_ready(self) -> bool:
        return self._sess.window_open()

    def poll_supervise(self, in_session: bool, trading_day) -> None:
        self._sup.tick(in_session=in_session, trading_day=trading_day)

    def set_context(self, fn) -> None:
        self._context_fn = fn


# ——— EMQ 绑定惰性加载（测试用 patch 打 mock，不依赖本机 .so）———

def _load_emd_binding():
    """惰性导入 EMQ pybind11 绑定（.so 按解释器 ABI 编译，本机无 .so 时 ImportError）。"""
    from src.strategy_framework.emd import emd_quote_api
    return emd_quote_api


def _make_spi(mod, gateway):
    """构造 QuoteSpi trampoline 实例（覆写回调，回投 gateway）。"""
    class _Spi(mod.QuoteSpi):
        def OnDepthMarketData(self, market_data, bid1_count, ask1_count):
            gateway._on_depth(market_data)

        def OnError(self, error_info):
            gateway._on_error(error_info)

        def OnSubMarketData(self, ticker, error_info, is_last):
            gateway._on_sub_result(ticker, error_info)

        def OnUnSubMarketData(self, ticker, error_info, is_last):
            gateway._on_unsub_result(ticker, error_info)
    return _Spi()


def _parse_host_port(addr: str, default_port: int) -> tuple[str, int]:
    """'host:port' → (host, port)；无端口用 default_port；空串/坏值/端口越界回 ('', default_port)。"""
    addr = (addr or "").strip()
    if not addr:
        return "", default_port
    if ":" in addr:
        host, _, port_s = addr.rpartition(":")
        try:
            port = int(port_s)
            if not (0 < port <= 65535):
                return host, default_port
            return host, port
        except ValueError:
            return host, default_port
    return addr, default_port


def _md_to_tick(md):
    """EMTMarketDataStruct → vnpy TickData（tick 契约见模块 docstring；未知交易所/无时刻返 None）。

    data_time(int64 YYYYMMDDHHMMSSsss) 按中国时刻解析——与 XTP 网关行为等值
    （strptime + replace(tzinfo=Asia/Shanghai)），供 _in_bar_session 中国时段判界。
    data_time<=0（盘前快照零值）前置丢弃——strptime("0") 会 ValueError。
    """
    if int(md.data_time) <= 0:
        return None
    from src.data_platform.tz import SHANGHAI
    from vnpy.trader.constant import Exchange
    from vnpy.trader.object import TickData

    # 交易所映射（EMQ_EXCHANGE_TYPE → vnpy 名）：BJGZ(5)→BSE；vnpy 无 BSE 则丢弃，
    # 绝不回落 SSE 产出 "XXX.SHSE" 错键（模块 docstring 点名警告的坑）。
    ex_name = {1: "SSE", 2: "SZSE", 5: "BSE"}.get(int(md.exchange_id))
    ex = getattr(Exchange, ex_name, None) if ex_name else None
    if ex is None:
        return None
    dt = datetime.strptime(str(md.data_time), "%Y%m%d%H%M%S%f").replace(tzinfo=SHANGHAI)
    tick = TickData(
        symbol=md.ticker,
        exchange=ex,
        datetime=dt,
        volume=md.qty,
        turnover=md.turnover,
        last_price=md.last_price,
        limit_up=md.upper_limit_price,
        limit_down=md.lower_limit_price,
        open_price=md.open_price,
        high_price=md.high_price,
        low_price=md.low_price,
        pre_close=md.pre_close_price,
        gateway_name="EMQ",
    )
    tick.bid_price_1, tick.bid_price_2, tick.bid_price_3, tick.bid_price_4, tick.bid_price_5 = md.bid[0:5]
    tick.ask_price_1, tick.ask_price_2, tick.ask_price_3, tick.ask_price_4, tick.ask_price_5 = md.ask[0:5]
    tick.bid_volume_1, tick.bid_volume_2, tick.bid_volume_3, tick.bid_volume_4, tick.bid_volume_5 = md.bid_qty[0:5]
    tick.ask_volume_1, tick.ask_volume_2, tick.ask_volume_3, tick.ask_volume_4, tick.ask_volume_5 = md.ask_qty[0:5]
    return tick


@register_md_gateway
class EmqMdGateway(MdGateway):
    """东方财富 EMQ 极速行情网关（批 63 Phase B）。

    EMQ QuoteApi 直连（无 vnpy 网关层）：CreateQuoteApi → RegisterSpi(PyQuoteSpi trampoline)
    → Login（同步阻塞，<0 失败）→ SubscribeMarketData。tick 走 SDK 回调线程 →
    OnDepthMarketData → _md_to_tick（EMTMarketDataStruct → vnpy TickData）→ 入队 →
    消费线程 cb(tick)（隔离 SDK 回调线程，满足「回调须快速返回防断线」）。
    """

    provider = "emt_emq"

    _TICK_QUEUE_MAX = 10000   # 有界队列：满则丢最新 tick + 计数（防消费停滞内存失控）

    def __init__(self, counters):
        from src.strategy_framework.runtime.alerts import make_alert
        self._alert = make_alert()
        # counters（hub SessionCounters）：P3 监督器（断流告警/续航）消费，P2 暂无监督故不存。
        self._mod = None            # emd_quote_api 绑定模块（connect 惰性加载，测试 mock）
        self._api = None            # QuoteApi 实例
        self._spi = None            # PyQuoteSpi trampoline 实例
        self._on_tick_cb = None
        self._connected = False
        self._login_err = 0
        self._q = queue.Queue(maxsize=self._TICK_QUEUE_MAX)
        self._worker = None
        self._dropped = 0

    # ——— MdGateway 抽象实现 ———

    def set_on_tick(self, cb) -> None:
        self._on_tick_cb = cb

    def connect(self, cred: dict, params: dict) -> None:
        mod = _load_emd_binding()
        self._mod = mod
        account = cred.get("emq_account", "") or ""
        pwd = cred.get("emq_password", "") or ""
        addr = params.get("emq_l1_host", "") or params.get("emq_l2_host", "") or ""
        ip, port = _parse_host_port(addr, default_port=8093)
        # 日志目录持久化（家目录，不被 systemd-tmpfiles 清 /tmp 扫掉——SDK 要求真实可写路径）
        log_dir = os.path.expanduser(os.path.join("~", ".quant", "emd_quote_logs"))
        os.makedirs(log_dir, exist_ok=True)
        self._api = mod.QuoteApi.CreateQuoteApi(log_dir, mod.EMQ_LOG_LEVEL.INFO, mod.EMQ_LOG_LEVEL.ERROR)
        self._spi = _make_spi(mod, self)
        self._api.RegisterSpi(self._spi)
        ret = self._api.Login(ip, port, account, pwd)
        self._login_err = ret
        self._connected = ret >= 0
        if not self._connected:
            # 登录失败 fail-fast（对齐 hub「建连异常 → exit 78」）：EMQ 无 supervisor 重登，
            # 静默 _connected=False 会让 hub 成「失聪僵尸」（占租约心跳却不工作）——raise 让
            # systemd 重启重试，而非半死存活。
            raise RuntimeError(f"EMQ 行情登录失败（Login 返回 {ret}，{ip}:{port}）")

    def subscribe(self, symbol: str) -> None:
        emq_ex = self._emq_ex_for(symbol)
        if emq_ex is None or self._api is None or not self._connected:
            return
        raw = symbol.rsplit(".", 1)[0]
        ret = self._api.SubscribeMarketData([raw], emq_ex)
        if ret != 0:
            logger.warning("[gw] EMQ 订阅请求失败 %s（返回 %s）", symbol, ret)
            self._alert("EMQ 订阅失败", f"{symbol} 订阅请求返回 {ret}", code="emq.sub-fail")

    def unsubscribe(self, symbol: str) -> None:
        emq_ex = self._emq_ex_for(symbol)
        if emq_ex is None or self._api is None or not self._connected:
            logger.debug("退订跳过 %s（未登录或未映射交易所，待重连后 diff 补齐）", symbol)
            return
        raw = symbol.rsplit(".", 1)[0]
        self._api.UnSubscribeMarketData([raw], emq_ex)
        logger.info("退订 %s（生命周期结束）", symbol)

    @property
    def connected(self) -> bool:
        return self._connected

    def start_ready(self) -> bool:
        return self._connected

    def poll_supervise(self, in_session: bool, trading_day) -> None:
        # EMQ 无每日连接窗（SDK「不支持过夜」）；无 OnDisconnected 回调——断线感知仅 OnError。
        # 运行期重连语义待 P3 staging 真连实证（Login 同步阻塞原语；SDK 未明示自动重连），
        # 本钩子暂不主动重登；仅周期清点 tick 丢弃计数（消费停滞可见化）。
        if self._dropped:
            logger.warning("[gw] EMQ tick 队列满丢弃 %d 条（消费停滞？）", self._dropped)
            self._dropped = 0

    # set_context 不覆写——EMQ 无监督器消费告警上下文，用 ABC 默认 no-op。

    # ——— 内部 ———

    def _emq_ex_for(self, symbol: str):
        """project 后缀（SHSE/SZSE/BSE）→ 绑定模块 EMQ_EXCHANGE_TYPE 枚举；未映射/未加载返 None。"""
        suffix = symbol.rsplit(".", 1)[-1]
        name = {"SHSE": "SH", "SZSE": "SZ", "BSE": "BJGZ"}.get(suffix)
        if name is None or self._mod is None:
            return None
        return getattr(self._mod.EMQ_EXCHANGE_TYPE, name)

    def _on_depth(self, md) -> None:
        """SDK 回调线程面（须快速返回）：映射后入队，消费线程再喂 cb。

        异常兜底（C++ trampoline 已 try/catch 双保险，此处再兜一层）：任何映射异常
        绝不穿透到 SDK 回调帧（EMQ 无 OnDisconnected，回调崩 = 整进程 SIGABRT）。
        """
        try:
            tick = _md_to_tick(md)
        except Exception:
            logger.exception("EMQ tick 映射异常（丢弃）")
            return
        if tick is None:
            return
        self._ensure_worker()   # 先确保消费线程在，再入队（满队列分支才不丢 worker 重启机会）
        try:
            self._q.put_nowait(tick)
        except queue.Full:
            self._dropped += 1

    def _on_error(self, err) -> None:
        eid = getattr(err, "error_id", None)
        msg = getattr(err, "error_msg", "")
        logger.error("[gw] EMQ OnError（error_id=%s）: %s", eid, msg)
        self._connected = False
        self._alert("EMQ 行情错误", f"error_id={eid} {msg}".strip(), code="emq.md-error")

    def _on_sub_result(self, ticker, error_info) -> None:
        """订阅确认回调（SDK 线程面）：error_id 非 0 = 服务器拒收，记日志+告警（订阅失败可见化）。"""
        eid = getattr(error_info, "error_id", None) if error_info else None
        code = getattr(ticker, "ticker", "?") if ticker else "?"
        if eid:
            logger.error("[gw] EMQ 订阅被拒 %s（error_id=%s）", code, eid)
            self._alert("EMQ 订阅失败", f"{code} error_id={eid}", code="emq.sub-fail")

    def _on_unsub_result(self, ticker, error_info) -> None:
        eid = getattr(error_info, "error_id", None) if error_info else None
        code = getattr(ticker, "ticker", "?") if ticker else "?"
        if eid:
            logger.warning("[gw] EMQ 退订异常 %s（error_id=%s）", code, eid)

    def _ensure_worker(self) -> None:
        # SDK 单回调线程（OnDepthMarketData 消息按序到达，文档「阻塞后续消息」暗示单线程），
        # 本方法仅在回调线程调用——无并发起线程竞态；is_alive 兜底防 worker 意外死亡后重建。
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._consume, name="emq-tick", daemon=True)
            self._worker.start()

    def _consume(self) -> None:
        while True:
            tick = self._q.get()
            cb = self._on_tick_cb
            if cb is not None:
                try:
                    cb(tick)
                except Exception:
                    logger.exception("EMQ on_tick 回调异常（消费线程）")


# ——— 加密行情网关（加密接入批，就绪——加密实盘未开通，真连接挂账）———

def _crypto_tick_ex(value: str):
    """加密 tick 交易所 duck-type（vnpy Exchange 无 BINANCE/OKX 成员，仿模块 docstring 契约只给 .value）。"""
    return type("_CryptoExchange", (), {"value": value})()


class _BaseCryptoMdGateway(MdGateway):
    """加密 MD 网关公共基类（币安/OKX 共享 symbol 双向翻译 + vnpy 空凭证 MD-only 骨架）。

    symbol 翻译：项目 `BTCUSDT.{EX}`（裸基+点后缀）↔ vnpy `BTCUSDT_SWAP_{EX}.GLOBAL`
    （带 _SWAP_* 后缀 + GLOBAL 交易所——vnpy 永续合约真实格式，Explore 实测）。
    """

    _SWAP_SUFFIX = ""      # 子类：_SWAP_BINANCE / _SWAP_OKX
    _EX_VALUE = ""         # 子类：BINANCE / OKX

    def __init__(self, counters):
        from vnpy.event import EventEngine
        from vnpy.trader.event import EVENT_LOG
        from src.strategy_framework.runtime.alerts import make_alert, make_guard
        self._ee = EventEngine()
        self._ee.start()
        self._gw = self._make_gateway(self._ee)
        self._on_tick_cb = None
        self.event_engines = (self._ee,)

        @make_guard("hub.on_log", make_alert())
        def _on_log(event):
            logger.info("[gw] %s", getattr(event.data, "msg", event.data))
        self._ee.register(EVENT_LOG, _on_log)

    def _make_gateway(self, ee):
        raise NotImplementedError

    def set_on_tick(self, cb) -> None:
        from vnpy.trader.event import EVENT_TICK

        def _wrap(event):
            tick = event.data
            if tick.symbol.endswith(self._SWAP_SUFFIX):
                tick.symbol = tick.symbol[:-len(self._SWAP_SUFFIX)]
            tick.exchange = _crypto_tick_ex(self._EX_VALUE)
            cb(tick)
        self._ee.register(EVENT_TICK, _wrap)

    def subscribe(self, symbol: str) -> None:
        from vnpy.trader.object import SubscribeRequest
        from vnpy.trader.constant import Exchange
        raw = symbol.rsplit(".", 1)[0]
        self._gw.subscribe(SubscribeRequest(symbol=raw + self._SWAP_SUFFIX, exchange=Exchange.GLOBAL))

    def unsubscribe(self, symbol: str) -> None:
        # vnpy 加密 gateway 无退订原语——退订靠重连后 diff 补齐（对齐 XtpMdGateway 非登录态跳过语义）
        logger.debug("加密退订 no-op %s（vnpy 无退订原语，重连后 diff 补齐）", symbol)

    @property
    def connected(self) -> bool:
        return True   # vnpy 加密 gateway 自带断线重连+重订阅，hub 无需重连沿检测

    def start_ready(self) -> bool:
        return True   # 24/7 常连（订阅靠 hub sm.poll/replay 周期重放，合约拉完后才真正生效）

    def poll_supervise(self, in_session: bool, trading_day) -> None:
        pass   # vnpy 自带断线重连，无额外监督


@register_md_gateway
class BinanceMdGateway(_BaseCryptoMdGateway):
    """币安 USDT 永续行情网关（空凭证 MD-only：on_query_contract 空 key 不启 TD/user stream）。"""

    provider = "binance_perp"
    _SWAP_SUFFIX = "_SWAP_BINANCE"
    _EX_VALUE = "BINANCE"

    def _make_gateway(self, ee):
        from vnpy_binance.linear_gateway import BinanceLinearGateway
        return BinanceLinearGateway(ee, "BINANCE_LINEAR")

    def connect(self, cred: dict, params: dict) -> None:
        setting = {
            "API Key": "", "API Secret": "",
            "Server": (params.get("server") or "REAL"),
            "Kline Stream": "False",
            "Proxy Host": params.get("proxy_host", ""),
            "Proxy Port": int(params.get("proxy_port", 0) or 0),
        }
        self._gw.connect(setting)


@register_md_gateway
class OkxMdGateway(_BaseCryptoMdGateway):
    """OKX 永续行情网关（MD-only：覆写 connect_ws_api 只连 public_api，跳过空凭证 private login 报错）。"""

    provider = "okx_perp"
    _SWAP_SUFFIX = "_SWAP_OKX"
    _EX_VALUE = "OKX"

    def _make_gateway(self, ee):
        from vnpy_okx.okx_gateway import OkxGateway
        gw = OkxGateway(ee, "OKX")

        def _connect_ws_only():
            # 只连 public_api（行情）；跳过 private_api/business_api（空凭证 login 反复报错）
            gw.public_api.connect(gw.server, gw.proxy_host, gw.proxy_port)
            from vnpy.trader.event import EVENT_TIMER
            gw.event_engine.register(EVENT_TIMER, gw.process_timer_event)

        gw.connect_ws_api = _connect_ws_only
        return gw

    def connect(self, cred: dict, params: dict) -> None:
        setting = {
            "API Key": "", "Secret Key": "", "Passphrase": "",
            "Server": (params.get("server") or "REAL"),
            "Proxy Host": params.get("proxy_host", ""),
            "Proxy Port": int(params.get("proxy_port", 0) or 0),
            "Spread Trading": "False",
            "Margin Currency": "",
        }
        self._gw.connect(setting)
