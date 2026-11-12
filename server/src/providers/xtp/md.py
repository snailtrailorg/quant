"""XTP MD 插件（P3 第一段：端口化包装；连接原语下一段搬入本包）。

MarketDataPort 契约见 `ports/market_data.py`；`poll_supervise(in_session, trading_day)`
的连接窗语义由编排层判定后传入（D32 §9.5）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class XtpMarketDataPort:
    def __init__(self, counters: Any, slot: Any = None) -> None:
        from src.providers.xtp.md_gateway import XtpMdGateway
        self.slot = slot
        self._gw = XtpMdGateway(counters)

    # ── 生命周期 ────────────────────────────────────────
    def connect(self, credentials: dict, params: dict) -> None:
        self._gw.connect(credentials, params)

    def close(self) -> None:
        self._gw.close()

    @property
    def connected(self) -> bool:
        return bool(self._gw.connected)

    def start_ready(self) -> bool:
        return bool(self._gw.start_ready())

    def set_context(self, ctx: Any) -> None:
        self._gw.set_context(ctx)

    def poll_supervise(self, in_session: bool, trading_day: bool) -> None:
        self._gw.poll_supervise(in_session=in_session, trading_day=trading_day)

    def event_threads_healthy(self) -> bool:
        return bool(self._gw.event_threads_healthy())

    # ── 数据面 ──────────────────────────────────────────
    def set_on_tick(self, cb: Callable) -> None:
        self._gw.set_on_tick(cb)

    def subscribe(self, symbols: list[str]) -> None:
        self._gw.subscribe(symbols)

    def unsubscribe(self, symbols: list[str]) -> None:
        self._gw.unsubscribe(symbols)

    def snapshot(self, symbol: str):
        return self._gw.snapshot(symbol)

    def __getattr__(self, item: str):
        # 未在端口契约内的旧面（hub 过渡期少量使用）直接透传，避免双份实现
        return getattr(self._gw, item)


def build_md(counters: Any, slot: Any = None) -> XtpMarketDataPort:
    return XtpMarketDataPort(counters, slot)
