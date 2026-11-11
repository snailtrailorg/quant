"""D32 P1b · LegacyExecutionPort（迁移期反腐蚀层，P3 删除）。

把现有 ExecutionAdapter + vnpy EventEngine 包装成 ports.ExecutionPort：
- 事件推送：subscribe_order/subscribe_trade 注册到 EventEngine，翻译+回调整体落在守卫内
  （vnpy 事件线程零保护，F-26）；
- 查询/撤单：P1b 生产仍走 adapter（trading._cancel_working_orders/reconcile 直接调），
  本类方法供端口完整性/等价测试与 P3 替换前对照；
- 本文件零 vnpy import：事件类型由调用方注入。
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Callable

from src.quant_common.domain import AccountState, Order, OrderReceipt, OrderState, Position, Trade
from src.strategy_framework.adapters import Order as LegacyOrder
from src.strategy_runner.legacy_translate import (
    to_domain_account,
    to_domain_order,
    to_domain_position,
    to_domain_trade,
)

logger = logging.getLogger(__name__)


class LegacyExecutionPort:
    def __init__(
        self,
        event_engine: Any,
        adapter_getter: Callable[[], Any],
        guard_factory: Callable[[str], Callable[[Callable], Callable]],
        event_order: str,
        event_trade: str,
        connected_getter: Callable[[], bool] | None = None,
    ) -> None:
        self._ee = event_engine
        self._get_adapter = adapter_getter
        self._guard_factory = guard_factory
        self._event_order = event_order
        self._event_trade = event_trade
        self._connected_getter = connected_getter or (lambda: False)
        self._subs: dict[tuple[str, int], Callable] = {}

    # ── 内部 ────────────────────────────────────────────────
    def _adapter(self, required: bool = True) -> Any:
        a = self._get_adapter()
        if a is None and required:
            raise RuntimeError("LegacyExecutionPort: adapter 未绑定")
        return a

    def _cid_of(self, a: Any, vt_orderid: str, *, db_fallback: bool = False) -> str:
        """反填 client_id（best-effort）：优先进程内 _vt2cid，重启后按 order_log 反查。

        db_fallback=False 用于事件推送（事件线程不做 DB 查询）；
        db_fallback=True 用于 query_orders/query_trades（重启清场时恢复平台身份）。
        """
        if not vt_orderid:
            return ""
        cid = ""
        if a is not None:
            mapping = getattr(a, "_vt2cid", None)
            if mapping is not None:
                lock = getattr(a, "_lock", None) or contextlib.nullcontext()
                try:
                    with lock:
                        cid = mapping.get(vt_orderid, "") or ""
                except Exception:
                    cid = ""
        if cid:
            return cid if isinstance(cid, str) else ""
        if not db_fallback:
            return ""
        # F-50 同款：重启后 _vt2cid 进程内丢失，用 order_log.vt_orderid 反查
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT client_order_id FROM order_log WHERE vt_orderid=%s "
                    "ORDER BY id DESC LIMIT 1", (vt_orderid,)).fetchone()
            return (row[0] or "") if row else ""
        except Exception as e:
            logger.warning("client_id 反查失败 vt=%s: %s", vt_orderid, e)
            return ""

    def _subscribe(self, event_type: str, cb: Callable, translate: Callable, name: str) -> None:
        key = (event_type, id(cb))
        if key in self._subs:
            return

        def fn(event: Any) -> None:
            a = self._get_adapter()          # 未绑定返回 None（永不抛）
            data = event.data
            vt = getattr(data, "vt_orderid", "") or ""
            cb(translate(data, self._cid_of(a, vt)))

        wrapped = self._guard_factory(name)(fn)
        self._ee.register(event_type, wrapped)
        self._subs[key] = wrapped

    # ── 生命周期 ────────────────────────────────────────────
    def connect(self) -> None:
        return None

    def close(self) -> None:
        return None

    @property
    def connected(self) -> bool:
        return bool(self._connected_getter())

    # ── 交易 ────────────────────────────────────────────────
    def send(self, order: Order) -> OrderReceipt:
        a = self._adapter()
        legacy = LegacyOrder(
            symbol=order.symbol,
            action=str(order.action),
            volume=order.volume,
            price=order.price,
            order_type=str(order.order_type),
            client_id=order.client_id,
        )
        # 每次调用取 adapter.send_order：main 的 _gated_send 闸门在实例属性上
        cid = getattr(a, "send_order")(legacy)
        return OrderReceipt(
            client_id=cid or "",
            accepted=bool(cid),
            reason="" if cid else "adapter 返回空委托号（委托未发出）",
        )

    def cancel(self, client_order_id: str) -> None:
        a = self._adapter()
        vt = a.get_vt_orderid(client_order_id) if client_order_id else None
        if not vt and client_order_id:
            # 重启后 _vt2cid 为空：按 order_log 的 client_order_id 反查 vt（F-50 逆路）
            try:
                from src.data_platform.db import get_conn
                with get_conn() as conn:
                    row = conn.execute(
                        "SELECT vt_orderid FROM order_log WHERE client_order_id=%s "
                        "ORDER BY id DESC LIMIT 1", (client_order_id,)).fetchone()
                vt = (row[0] or None) if row else None
            except Exception as e:
                logger.warning("cancel vt 反查失败 cid=%s: %s", client_order_id, e)
        if not vt:
            raise LookupError(f"cancel: 无法解析 client_order_id={client_order_id!r}")
        a.cancel_order(vt)

    # ── 推送 ────────────────────────────────────────────────
    def subscribe_order(self, cb: Callable[[OrderState], None]) -> None:
        self._subscribe(self._event_order, cb, to_domain_order, "worker.on_order_status")

    def subscribe_trade(self, cb: Callable[[Trade], None]) -> None:
        self._subscribe(self._event_trade, cb, to_domain_trade, "hub.on_trade")

    # ── 查询 ────────────────────────────────────────────────
    def query_positions(self) -> list[Position]:
        a = self._adapter()
        return [to_domain_position(p) for p in (a.query_position() or [])]

    def query_account(self) -> AccountState | None:
        a = self._adapter()
        rows = a.query_account() or []
        if not rows:
            return None
        if len(rows) > 1:
            logger.warning("LegacyExecutionPort.query_account 返回 %d 行（per-account 预期 0/1），取首", len(rows))
        return to_domain_account(rows[0])

    def query_orders(self) -> list[OrderState]:
        a = self._adapter()
        out: list[OrderState] = []
        for od in (a.query_orders() or []):
            try:
                out.append(to_domain_order(od, self._cid_of(a, getattr(od, "vt_orderid", "") or "", db_fallback=True)))
            except Exception as e:
                logger.warning("port.query_orders 翻译跳过: %s", e)
        return out

    def query_trades(self) -> list[Trade]:
        a = self._adapter()
        out: list[Trade] = []
        for td in (a.query_trades() or []):
            try:
                out.append(to_domain_trade(td, self._cid_of(a, getattr(td, "vt_orderid", "") or "", db_fallback=True)))
            except Exception as e:
                logger.warning("port.query_trades 翻译跳过: %s", e)
        return out

    def resolve_vt_order_id(self, client_order_id: str) -> str | None:
        return self._adapter().get_vt_orderid(client_order_id)
