"""D32 领域模型 · 成交与持仓（batch 172a）。

字段对齐 `adapters.Position`（7 字段），`available` 由 `volume - frozen` 派生为属性，
不存字段——避免两份真源（T+1 可卖口径单源）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import Direction


@dataclass(frozen=True)
class Trade:
    tradeid: str             # 存 vt_tradeid（trade_ref 唯一键）
    orderid: str             # 存 vt_orderid（券商侧对账键；裸 orderid 不在此，P3 插件按此填）
    symbol: str
    direction: Direction
    price: float
    volume: float
    datetime: datetime | None = None
    client_id: str = ""         # best-effort：_vt2cid 反填；trade_log 认领用


@dataclass(frozen=True)
class Position:
    symbol: str
    volume: int
    avg_price: float
    pnl: float = 0.0
    direction: Direction = Direction.LONG
    frozen: int = 0             # 冻结量（含在途卖单）
    yd_volume: int = 0          # 昨仓

    @property
    def available(self) -> int:
        """可卖量 = 持仓 - 冻结。与现有 `SUM(volume - frozen)` 同式（不夹紧，
        避免在 P1a 引入新语义；负数是否可能见 172b 的对账确认项）。"""
        return self.volume - self.frozen
