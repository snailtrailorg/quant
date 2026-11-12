"""vnpy/XTP SDK 对象 → 领域模型（插件内翻译；不依赖迁移期 ACL）。"""
from __future__ import annotations

from typing import Any

from src.quant_common.domain import (
    AccountState, Direction, OrderState, OrderStatus, Position, Trade,
)

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
        symbol=getattr(d, "symbol", "") or "",
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
        symbol=getattr(d, "symbol", "") or "",
        direction=direction_of(d),
        price=float(getattr(d, "price", 0) or 0),
        volume=float(getattr(d, "volume", 0) or 0),
        datetime=getattr(d, "datetime", None), client_id=client_id or "",
    )


def to_position(p: Any) -> Position:
    name = getattr(getattr(p, "direction", None), "name", "LONG").lower()
    return Position(
        symbol=getattr(p, "vt_symbol", "") or getattr(p, "symbol", "") or "",
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
