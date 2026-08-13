"""策略框架 · ExecutionAdapter 执行适配器。

场内 XTP（可转债/ETF/A 股股票，中泰 XTP 通道）/ 加密币安/OKX 两种实现。

实盘下单三级开关（AND）：
1. .env ENABLE_LIVE_TRADING（总闸，settings.is_live_trading_enabled）
2. Web live_trading_config 分项（risk.is_live_trading_allowed，在 check_order 前置检查）
   分项：convertible/etf/astock/binance_perp/okx_perp
3. strategy_config.enabled + backtest_verified（策略级，scheduler 层）
A 股股票走 XTPAdapter（中泰 XTP 能交易 A 股），受 astock 分项开关控制。
"""

from __future__ import annotations
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time


@dataclass
class Order:
    symbol: str
    action: str  # BUY / SELL
    volume: int = 0
    price: float = 0.0
    order_type: str = "limit"  # limit / market
    client_id: str = ""


@dataclass
class Position:
    symbol: str
    volume: int
    avg_price: float
    pnl: float = 0.0


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


# --- 场内 XTP 适配器（vnpy_xtp 网关，可转债/ETF/A 股股票） ---

def _vnpy_exchange(ex: str):
    """项目交易所后缀 -> vnpy Exchange 枚举（延迟 import）。"""
    from vnpy.trader.constant import Exchange
    mapping = {"SHSE": Exchange.SSE, "SSE": Exchange.SSE, "SZSE": Exchange.SZSE}
    return mapping.get(ex.upper(), Exchange.SSE)


class XTPAdapter(ExecutionAdapter):
    """中泰 XTP 交易适配器（底层 vnpy_xtp.XtpGateway）。

    交易品种：可转债/ETF/A 股股票（中泰 XTP 通道），受三级开关控制
    （astock/etf/convertible 分项）。vnpy 4.0 查询事件驱动：调 gateway.query_position/account()
    后异步推 EVENT_POSITION/ACCOUNT，本类注册监听收集到缓存，query_xxx 触发后轮询等结果。
    query_orders/trades 纯靠事件推送（XTP 网关无主动查委托/成交接口）。
    """

    def __init__(self, gateway=None, event_engine=None):
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
        client_id = order.client_id or f"c{self._cid_seq}"
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
            # 退化：没缓存时用 order_id 直接试（可能失败）
            sym, ex = self.parse_vt_symbol(order_id)
            req = CancelRequest(orderid=order_id, symbol=sym, exchange=_vnpy_exchange(ex))
        self._gateway.cancel_order(req)

    # ── 查询（事件驱动，触发后轮询等结果） ──

    def query_position(self) -> list[Position]:
        if self._gateway is None:
            return []
        with self._lock:
            before = set(self._positions.keys())
        self._gateway.query_position()
        self._wait_update(self._positions, before, timeout=2.0)
        with self._lock:
            return [
                Position(
                    symbol=p.vt_symbol,
                    volume=int(p.volume),
                    avg_price=float(p.price),
                    pnl=float(getattr(p, "pnl", 0.0) or 0.0),
                )
                for p in self._positions.values()
            ]

    def query_account(self) -> list:
        if self._gateway is None:
            return []
        with self._lock:
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
    def _wait_update(cache: dict, before: set, timeout: float = 2.0) -> None:
        """轮询等事件推送更新缓存（vnpy 查询是异步）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if set(cache.keys()) != before:
                return
            time.sleep(0.1)


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


# --- 适配器工厂 ---

def create_adapter(adapter_type: str, gateway=None, event_engine=None) -> ExecutionAdapter:
    """创建适配器实例。XTPAdapter 可传 event_engine 注册事件监听。
    A 股股票/可转债/ETF 统一走 'xtp'（中泰 XTP 通道）。
    """
    mapping = {
        "xtp": XTPAdapter,
        "binance_perp": CryptoPerpAdapter,
        "okx_perp": CryptoPerpAdapter,
    }
    cls = mapping.get(adapter_type)
    if cls is None:
        raise ValueError(f"未知适配器类型: {adapter_type}，可选: {list(mapping.keys())}")
    if cls is XTPAdapter:
        return cls(gateway=gateway, event_engine=event_engine)
    return cls(gateway=gateway)
