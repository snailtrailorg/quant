"""D32 P1b · legacy 对象 → 领域模型翻译（迁移期，P3 随 ACL 删除）。

零 vnpy import：vnpy 的 Status/Direction 只按 .name 字符串映射——S1 基线只减不增。
只服务 P1b 的 vnpy EventEngine 事件与 ExecutionAdapter 查询结果；P3 插件内部有
自己的 SDK 对象翻译（providers/xtp/translate.py），本文件删除。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from src.quant_common.domain import (
    AccountState,
    Direction,
    OrderState,
    OrderStatus,
    Position,
    Trade,
)

# vnpy Status.name → 领域状态（不 import vnpy.constant）
_STATUS_MAP = {
    "SUBMITTING": OrderStatus.SUBMITTED,
    "NOTTRADED": OrderStatus.NOT_TRADED,
    "PARTTRADED": OrderStatus.PARTIAL,
    "ALLTRADED": OrderStatus.ALL_TRADED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
}

_DIRECTIONS = {
    "LONG": Direction.LONG,
    "SHORT": Direction.SHORT,
    "NET": Direction.NET,
}


def status_of(d: Any) -> OrderStatus:
    """vnpy Status → 领域状态；缺失/未知 fail-closed（不得默认成在场态）。"""
    st = getattr(d, "status", None)
    if isinstance(st, OrderStatus):
        return st
    name = getattr(st, "name", None)
    if name in _STATUS_MAP:
        return _STATUS_MAP[name]
    if isinstance(st, str):
        try:
            return OrderStatus(st)
        except ValueError:
            pass
    raise ValueError(f"未知委托状态（fail-closed）: {st!r}")


def _direction_of(obj: Any) -> Direction:
    """vnpy/legacy Direction → 领域 Direction；None/未知回落 NET。

    NET 对 Trade 的作用：保住 write_trade_log「非 LONG 即 SELL」的现语义。
    """
    d = getattr(obj, "direction", None)
    if isinstance(d, Direction):
        return d
    name = getattr(d, "name", None)
    if name in _DIRECTIONS:
        return _DIRECTIONS[name]
    value = getattr(d, "value", None)
    if isinstance(value, str) and value in Direction._value2member_map_:
        return Direction(value)
    if isinstance(d, str) and d in Direction._value2member_map_:
        return Direction(d)
    return Direction.NET


def to_domain_order(d: Any, client_id: str = "") -> OrderState:
    """vnpy OrderData / legacy 查询对象 → OrderState（字段名一一对应）。"""
    if isinstance(d, OrderState):
        return d if not client_id or d.client_id == client_id else replace(d, client_id=client_id)
    return OrderState(
        orderid=getattr(d, "orderid", "") or "",
        vt_orderid=getattr(d, "vt_orderid", "") or "",
        symbol=getattr(d, "symbol", "") or "",
        direction=_direction_of(d),
        status=status_of(d),
        volume=float(getattr(d, "volume", 0) or 0),
        traded=float(getattr(d, "traded", 0) or 0),
        price=float(getattr(d, "price", 0) or 0),
        datetime=getattr(d, "datetime", None),
        client_id=client_id or "",
    )


def to_domain_trade(d: Any, client_id: str = "") -> Trade:
    """vnpy TradeData → Trade。只取 vt 键（不回落裸 id，见 172b v4 契约）。"""
    if isinstance(d, Trade):
        return d if not client_id or d.client_id == client_id else replace(d, client_id=client_id)
    return Trade(
        tradeid=getattr(d, "vt_tradeid", "") or "",
        orderid=getattr(d, "vt_orderid", "") or "",
        symbol=getattr(d, "symbol", "") or "",
        direction=_direction_of(d),
        price=float(getattr(d, "price", 0) or 0),
        volume=float(getattr(d, "volume", 0) or 0),
        datetime=getattr(d, "datetime", None),
        client_id=client_id or "",
    )


def to_domain_position(p: Any) -> Position:
    """legacy adapters.Position / vnpy PositionData → 领域 Position。"""
    return Position(
        symbol=getattr(p, "symbol", "") or "",
        volume=int(getattr(p, "volume", 0) or 0),
        avg_price=float(getattr(p, "avg_price", getattr(p, "price", 0)) or 0),
        pnl=float(getattr(p, "pnl", 0) or 0),
        direction=_direction_of(p),
        frozen=int(getattr(p, "frozen", 0) or 0),
        yd_volume=int(getattr(p, "yd_volume", 0) or 0),
    )


def to_domain_account(a: Any) -> AccountState | None:
    """vnpy/legacy 单账户 → AccountState；available 优先取源对象（vnpy 同式 balance-frozen）。"""
    if a is None:
        return None
    balance = float(getattr(a, "balance", 0) or 0)
    frozen = float(getattr(a, "frozen", 0) or 0)
    available = getattr(a, "available", None)
    return AccountState(
        accountid=str(getattr(a, "accountid", "") or ""),
        balance=balance,
        available=float(available) if available is not None else balance - frozen,
        frozen=frozen,
        margin=float(getattr(a, "margin", 0) or 0),
    )
