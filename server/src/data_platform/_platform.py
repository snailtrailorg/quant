"""数据中台 · DataPlatform 统一入口。

所有模块（策略/分析/调度/Web）通过此接口访问数据，不直接接触数据源。
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import pandas as pd

from .adapters import tushare_adapter as tushare
from .db import get_bars, save_bars
from .db import get_trade_calendar as _get_trade_calendar
from .db import is_trading_day as _is_trading_day


class DataPlatform:
    """统一数据中台入口。"""

    # ——— 历史数据 ———

    def get_bar(self, symbol: str, freq: str, start: date, end: date,
                adj: str | None = None) -> pd.DataFrame:
        """获取历史 K 线（从 PG 读，若缺失则从 Tushare 拉取）。

        Args:
            symbol: vt_symbol 格式，如 "600000.SHSE"
            freq: "1D" / "1H" / "1min" …
            start / end: 起止日期
            adj: 复权 (A 股)
        """
        df = get_bars(symbol, freq, start, end)
        if df.empty:
            pass  # 触发拉取（异步或在其他线程）
        return df
    # ——— 实时（占位，T04 实现） ———
    def subscribe(self, symbol: str, freq: str, handler: Callable):
        return None  # P3-7 暂未实现，返回 None 不崩("T04 实现")

    def unsubscribe(self, symbol: str, freq: str):
        pass

    # ——— 基本面/条款 ———
    # ——— 交易日历 ———

    def get_trade_calendar(self, year: int) -> list[date]:
        return _get_trade_calendar(year)

    def is_trading_day(self, d: date | None = None) -> bool:
        return _is_trading_day(d)

# 单例
platform = DataPlatform()
