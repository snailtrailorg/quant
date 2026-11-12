"""XTP TD 插件：XtpTdApi + ThinTdGateway → ExecutionPort（D32 §9.2/§9.4）。

- 传输（vnpy_xtp/vnpy 常量与对象）全部经 `_transport` **懒导入**；本模块导入期零 vnpy。
- 号段/连接窗/订单前缀由编排层经 `slot` 传入，插件照用不改。
- 同 client_id 单 TD 会话由 DB 租约保证（session_slot）；本模块只负责会话内行为。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from src.quant_common.domain import (
    AccountState, Order, OrderReceipt, OrderState, Position, Trade,
)

from . import _transport, translate

logger = logging.getLogger(__name__)

POSITION_STABLE_WINDOW_S = 3.0
ACCOUNT_WAIT_S = 2.0


def _split_symbol(vt: str) -> tuple[str, str]:
    if "." not in vt:
        return vt, ""
    sym, ex = vt.rsplit(".", 1)
    return sym, ex.upper()


class XtpExecutionPort:
    def __init__(self, event_engine, gateway, td_api, slot,
                 guard_factory: Callable[[str], Callable[[Callable], Callable]],
                 setting: dict | None = None) -> None:
        self._ee = event_engine
        self._gw = gateway
        self._api = td_api
        self.slot = slot
        self._setting = setting or {}
        self._guard = guard_factory
        self._orders: dict[str, Any] = {}
        self._trades: dict[str, Any] = {}
        self._positions: dict[str, Any] = {}
        self._accounts: dict[str, Any] = {}
        self._cid2vt: dict[str, str] = {}
        self._vt2cid: dict[str, str] = {}
        self._seq = 0
        self._lock = threading.Lock()
        ev = _transport.events()
        self._ee.register(ev["order"], self._on_order)
        self._ee.register(ev["trade"], self._on_trade)
        self._ee.register(ev["position"], self._on_position)
        self._ee.register(ev["account"], self._on_account)

    # ── 事件缓存 ──────────────────────────────────────────
    def _on_order(self, event: Any) -> None:
        d = event.data
        with self._lock:
            self._orders[d.vt_orderid] = d

    def _on_trade(self, event: Any) -> None:
        d = event.data
        with self._lock:
            self._trades[d.vt_tradeid] = d

    def _on_position(self, event: Any) -> None:
        d = event.data
        with self._lock:
            self._positions[d.vt_positionid] = d

    def _on_account(self, event: Any) -> None:
        d = event.data
        with self._lock:
            self._accounts[d.accountid] = d

    # ── 生命周期 ──────────────────────────────────────────
    def connect(self) -> None:
        if not self.slot.window_open:
            logger.info("XTP TD 窗关：跳过建连（等窗开沿）")
            return
        self._gw.connect(self._setting)

    def close(self) -> None:
        try:
            if getattr(self._api, "connect_status", False):
                self._api.exit()
        except Exception:   # noqa: BLE001 —— 退出路径禁抛
            pass

    @property
    def connected(self) -> bool:
        return bool(getattr(self._api, "connect_status", False))

    # ── 交易 ──────────────────────────────────────────────
    def send(self, order: Order) -> OrderReceipt:
        obj = _transport.objects()
        c = _transport.constants()
        sym, ex = _split_symbol(order.symbol)
        self._seq += 1
        client_id = order.client_id or f"{self.slot.order_prefix}c{self._seq}"
        req = obj.OrderRequest(
            symbol=sym, exchange=_transport.exchange_of(ex),
            direction=c.Direction.LONG if str(order.action).upper().endswith("BUY") else c.Direction.SHORT,
            type=c.OrderType.MARKET if str(order.order_type).lower().endswith("market") else c.OrderType.LIMIT,
            volume=order.volume, price=order.price, offset=c.Offset.NONE, reference=client_id,
        )
        vt = self._gw.send_order(req)
        orderid_part = vt.rsplit(".", 1)[-1] if vt else ""
        if not vt or orderid_part == "0":
            logger.error("网关拒绝/未发出委托（vt=%r symbol=%s）", vt, order.symbol)
            return OrderReceipt(client_id="", accepted=False, reason="网关未受理（空委托号）")
        with self._lock:
            self._cid2vt[client_id] = vt
            self._vt2cid[vt] = client_id
        return OrderReceipt(client_id=client_id, accepted=True)

    def cancel(self, client_order_id: str) -> None:
        obj = _transport.objects()
        with self._lock:
            vt = self._cid2vt.get(client_order_id, client_order_id)
            od = self._orders.get(vt)
        if od is not None:
            req = obj.CancelRequest(orderid=od.orderid, symbol=od.symbol, exchange=od.exchange)
        else:
            pure = client_order_id.rsplit(".", 1)[-1] if "." in client_order_id else client_order_id
            if not pure.isdigit():
                raise LookupError(f"撤单无法解析 orderid: {client_order_id!r}")
            logger.warning("撤单退化：无缓存，纯 orderid %s 盲撤", pure)
            req = obj.CancelRequest(orderid=pure, symbol="", exchange=_transport.exchange_of(""))
        self._gw.cancel_order(req)

    def resolve_vt_order_id(self, client_order_id: str) -> str | None:
        with self._lock:
            return self._cid2vt.get(client_order_id)

    # ── 推送 ──────────────────────────────────────────────
    def subscribe_order(self, cb: Callable[[OrderState], None]) -> None:
        def fn(event: Any) -> None:
            d = event.data
            with self._lock:
                cid = self._vt2cid.get(getattr(d, "vt_orderid", ""), "")
            cb(translate.to_order_state(d, cid))
        self._ee.register(_transport.events()["order"], self._guard("xtp.on_order")(fn))

    def subscribe_trade(self, cb: Callable[[Trade], None]) -> None:
        def fn(event: Any) -> None:
            d = event.data
            with self._lock:
                cid = self._vt2cid.get(getattr(d, "vt_orderid", ""), "")
            cb(translate.to_trade(d, cid))
        self._ee.register(_transport.events()["trade"], self._guard("xtp.on_trade")(fn))

    # ── 查询 ──────────────────────────────────────────────
    def query_positions(self) -> list[Position]:
        with self._lock:
            self._positions.clear()
        self._gw.query_position()
        deadline = time.time() + POSITION_STABLE_WINDOW_S
        prev = None
        keys = frozenset()
        while time.time() < deadline:
            time.sleep(0.1)
            with self._lock:
                keys = frozenset(self._positions.keys())
            if keys and keys == prev:
                break
            prev = keys
        with self._lock:
            return [translate.to_position(p) for p in self._positions.values()]

    def query_account(self) -> AccountState | None:
        with self._lock:
            self._accounts.clear()
            before = set(self._accounts)
        self._gw.query_account()
        deadline = time.time() + ACCOUNT_WAIT_S
        while time.time() < deadline:
            time.sleep(0.1)
            with self._lock:
                if set(self._accounts) != before:
                    break
        with self._lock:
            rows = list(self._accounts.values())
        return translate.to_account(rows[0]) if rows else None

    def query_orders(self) -> list[OrderState]:
        with self._lock:
            items = list(self._orders.items())
        with self._lock:
            cids = {vt: self._vt2cid.get(vt, "") for vt, _ in items}
        return [translate.to_order_state(d, cids.get(vt, "")) for vt, d in items]

    def query_trades(self) -> list[Trade]:
        with self._lock:
            items = list(self._trades.items())
        return [translate.to_trade(d, self._vt2cid.get(getattr(d, "vt_orderid", ""), ""))
                for _vt, d in items]

# ── 构建（供 plugin.build 调用；此处集中所有 SDK 类装配）────────────

def make_gateway(event_engine, name: str = "XTP"):
    """ThinTdGateway：TD-only 壳（零 MD/零合约表；事件转发 + 抽象方法转发 td_api）。"""
    Base = _transport.base_gateway_cls()
    obj = _transport.objects()

    class ThinTdGateway(Base):
        def connect(self, s: dict) -> None:
            self.td_api.connect(s["账号"], s["密码"], int(s["客户号"]), s["交易地址"],
                                int(s["交易端口"]), s.get("授权码", ""), 3)

        def subscribe(self, req) -> None:
            pass

        def write_error(self, msg: str, error: dict | None = None) -> None:
            try:
                detail = str((error or {}).get("error_msg") or (error or {}).get("error_id") or "") \
                    if isinstance(error, dict) else ""
                text = f"{msg}：{detail}" if detail else msg
                logger.error("TD 报错: %s", text)
                self.on_log(obj.LogData(msg=text, gateway_name=self.gateway_name))
            except Exception:   # noqa: BLE001 —— 回调链路禁抛
                pass

        def send_order(self, req):
            return self.td_api.send_order(req)

        def cancel_order(self, req) -> None:
            self.td_api.cancel_order(req)

        def query_account(self) -> None:
            self.td_api.query_account()

        def query_position(self) -> None:
            self.td_api.query_position()

        def close(self) -> None:
            try:
                if getattr(self.td_api, "connect_status", False):
                    self.td_api.exit()
            except Exception:   # noqa: BLE001
                pass

    return ThinTdGateway(event_engine, name)


def build_td(event_engine, slot, setting: dict,
             guard_factory: Callable[[str], Callable[[Callable], Callable]]):
    """组装 TD 运行时：gateway + api + ExecutionPort（窗关时不建连）。"""
    gw = make_gateway(event_engine)
    api = _transport.td_api_cls()(gw)
    gw.td_api = api
    port = XtpExecutionPort(event_engine, gw, api, slot, guard_factory, setting)
    port.connect()
    return port
