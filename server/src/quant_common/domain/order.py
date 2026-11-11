"""D32 领域模型 · 委托与委托回报（batch 172a）。

字段对齐 `strategy_framework/adapters.py` 现行 `Order`（6 字段），
不引入 offset/reference——A 股现货 `Offset.NONE` 恒值，`reference` 在
client_order_id 前缀里（`t{tid}:e{epoch}:c{n}`），均非独立领域字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import Action, Direction, OrderStatus, OrderType


@dataclass(frozen=True)
class Order:
    symbol: str
    action: Action
    volume: float = 0           # A 股=int 股数；加密=float（可下 0.001）
    price: float = 0.0
    order_type: OrderType = OrderType.LIMIT
    client_id: str = ""         # 编排层生成：t{tid}:e{epoch}:c{n}


@dataclass(frozen=True)
class OrderReceipt:
    """下单受理回执。accepted=False 时 reason 必非空。"""

    client_id: str
    accepted: bool
    reason: str = ""


@dataclass(frozen=True)
class OrderState:
    """委托的当前状态（推送与查询共用）。"""

    orderid: str
    vt_orderid: str
    symbol: str
    direction: Direction
    status: OrderStatus
    volume: float
    traded: float
    price: float
    datetime: datetime | None = None
    client_id: str = ""         # best-effort：平台侧稳定身份（P1b 由 _vt2cid 反填，P3 由插件填）
