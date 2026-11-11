"""D32 领域模型 · 行情（batch 172a）。

Tick 对齐 `md_gateway.py` 头注的 tick 契约（duck typing，vnpy TickData 原生满足）：
核心字段平铺（last_price/datetime/volume/turnover/exchange），盘口五档用定长元组
（1..5 档），避免暴露 vnpy 的 `bid_price_1..5` 具名字段。

时点律：Tick.datetime 必须 tz-aware Asia/Shanghai（中国时刻，用于时段判界与翻日）；
Bar.ts 必须 UTC aware（落库口径）。二者不同源，不可互换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Tick:
    symbol: str
    exchange: str                       # "SSE"/"SZSE"/"BINANCE"/"OKX"（vnpy 枚举 value 口径）
    datetime: datetime                  # tz-aware Asia/Shanghai
    last_price: float
    volume: float = 0
    turnover: float = 0
    open_price: float = 0
    high_price: float = 0
    low_price: float = 0
    pre_close: float = 0
    limit_up: float = 0
    limit_down: float = 0
    bid_price: tuple[float, ...] = field(default_factory=tuple)    # 买 1..5
    ask_price: tuple[float, ...] = field(default_factory=tuple)    # 卖 1..5
    bid_volume: tuple[float, ...] = field(default_factory=tuple)
    ask_volume: tuple[float, ...] = field(default_factory=tuple)

    def best_bid(self) -> float | None:
        return self.bid_price[0] if self.bid_price else None

    def best_ask(self) -> float | None:
        return self.ask_price[0] if self.ask_price else None


@dataclass(frozen=True)
class Bar:
    """bar 契约 11 字段（`quant_common/contract.py` BAR_COLUMNS 同序）。

    volume=股/币（非手）；amount=元/USDT；ts 必须 UTC aware。
    库中永远存不复权价 + adj_factor 旁挂（禁 close_qfq 之类列）。
    """

    symbol: str
    freq: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    adj_factor: float | None = None
    source: str = ""
