"""vnpy/XTP SDK 对象 → 领域模型（插件内翻译；不依赖迁移期 ACL）。"""
from __future__ import annotations

import logging
from typing import Any

from src.quant_common.domain import (
    AccountState, Direction, OrderState, OrderStatus, Position, Trade,
)
from src.quant_common.symbology import canonical

logger = logging.getLogger(__name__)


def _canonical_symbol(obj: Any) -> str:
    """批175 L1：vnpy 的 `symbol` 是**裸码**（`600036`）、`vt_symbol` 才带后缀；
    统一归一到项目 vt（`.SSE→.SHSE`）。空值保持空串；不可归一保留原值 + warning
    （never-raise 但 never-silent——回报链在 on_order 回调线程，抛错会崩实盘）。"""
    # 优先 `symbol`：真实 vnpy 是裸码（拼 exchange.value）；fixture/项目口径可能已带后缀。
    # 不用 `vt_symbol` 优先——它由 vnpy 按 `symbol+"."+exchange.value` 生成，symbol 已带后缀时会变脏。
    s = getattr(obj, "symbol", "") or ""
    ex = getattr(getattr(obj, "exchange", None), "value", "") or ""
    if s and "." in s:
        vt = s
    elif s and ex:
        vt = f"{s}.{ex}"
    else:
        vt = getattr(obj, "vt_symbol", "") or s
    if not vt:
        return ""
    try:
        return canonical(vt)
    except ValueError as e:
        logger.warning("translate: symbol 不可归一（保留原值）%s: %s", vt, e)
        return vt

_STATUS = {
    "SUBMITTING": OrderStatus.SUBMITTED,
    "NOTTRADED": OrderStatus.NOT_TRADED,
    "PARTTRADED": OrderStatus.PARTIAL,
    "ALLTRADED": OrderStatus.ALL_TRADED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
}
_DIR = {"LONG": Direction.LONG, "SHORT": Direction.SHORT, "NET": Direction.NET}


def status_of(obj: Any) -> OrderStatus:
    st = getattr(obj, "status", None)
    if isinstance(st, OrderStatus):
        return st
    name = getattr(st, "name", None)
    if name in _STATUS:
        return _STATUS[name]
    if isinstance(st, str):
        try:
            return OrderStatus(st)
        except ValueError:
            pass
    raise ValueError(f"未知委托状态（fail-closed）: {st!r}")


def direction_of(obj: Any) -> Direction:
    d = getattr(obj, "direction", None)
    if isinstance(d, Direction):
        return d
    name = getattr(d, "name", None)
    if name in _DIR:
        return _DIR[name]
    val = getattr(d, "value", None)
    if isinstance(val, str) and val in Direction._value2member_map_:
        return Direction(val)
    return Direction.NET


def to_order_state(d: Any, client_id: str = "") -> OrderState:
    return OrderState(
        orderid=getattr(d, "orderid", "") or "",
        vt_orderid=getattr(d, "vt_orderid", "") or "",
        symbol=_canonical_symbol(d),      # 批175：裸码/vt 统一到 vt
        direction=direction_of(d), status=status_of(d),
        volume=float(getattr(d, "volume", 0) or 0),
        traded=float(getattr(d, "traded", 0) or 0),
        price=float(getattr(d, "price", 0) or 0),
        datetime=getattr(d, "datetime", None), client_id=client_id or "",
    )


def to_trade(d: Any, client_id: str = "") -> Trade:
    return Trade(
        tradeid=getattr(d, "vt_tradeid", "") or "",
        orderid=getattr(d, "vt_orderid", "") or "",
        symbol=_canonical_symbol(d),      # 批175：裸码/vt 统一到 vt
        direction=direction_of(d),
        price=float(getattr(d, "price", 0) or 0),
        volume=float(getattr(d, "volume", 0) or 0),
        datetime=getattr(d, "datetime", None), client_id=client_id or "",
    )


def to_position(p: Any) -> Position:
    name = getattr(getattr(p, "direction", None), "name", "LONG").lower()
    return Position(
        symbol=_canonical_symbol(p),      # 批175：.SSE→.SHSE
        volume=int(getattr(p, "volume", 0) or 0),
        avg_price=float(getattr(p, "price", 0) or 0),
        pnl=float(getattr(p, "pnl", 0.0) or 0.0),
        direction="direction_" + name,
        frozen=int(getattr(p, "frozen", 0) or 0),
        yd_volume=int(getattr(p, "yd_volume", 0) or 0),
    )


def to_account(a: Any) -> AccountState | None:
    if a is None:
        return None
    return AccountState(
        accountid=str(getattr(a, "accountid", "") or ""),
        balance=float(getattr(a, "balance", 0) or 0),
        available=float(getattr(a, "available", 0) or 0),
        frozen=float(getattr(a, "frozen", 0) or 0),
        margin=float(getattr(a, "margin", 0) or 0),
    )
