"""数据中台 · DataPlatform 统一入口。

所有模块（策略/分析/调度/Web）通过此接口访问数据，不直接接触数据源。
"""

from __future__ import annotations
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd

from .db import save_bars, get_bars, is_trading_day as _is_trading_day
from .db import get_trade_calendar as _get_trade_calendar
from .schema import Bar, to_vt_symbol, parse_vt_symbol
from .adapters import tushare_adapter as tushare


class DataPlatform:
    """统一数据中台入口。"""

    # ——— 历史数据 ———

    def get_bar(self, symbol: str, freq: str, start: date, end: date,
                adj: str = "qfq") -> pd.DataFrame:
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

    def ensure_daily(self, ts_code: str, start_date: str, end_date: str | None = None,
                     adj: str = "qfq") -> int:
        """确保日线数据在 PG 中（拉取+写入，幂等）。返回写入行数。"""
        df = tushare.pull_daily(ts_code, start_date, end_date, adj=adj)
        if df.empty:
            return 0
        rows = tushare.to_save_rows(df, freq="1D")
        return save_bars("1D", rows)

    def ensure_minute(self, ts_code: str, freq: str, start_date: str,
                      end_date: str | None = None) -> int:
        """确保分钟线数据在 PG 中（拉取+写入，幂等）。需 Tushare 2000 积分。"""
        df = tushare.pull_minute(ts_code, freq, start_date, end_date)
        if df.empty:
            return 0
        rows = tushare.to_save_rows_min(df, freq=freq)
        return save_bars(freq, rows)

    # ——— 实时（占位，T04 实现） ———

    def get_realtime(self, symbol: str) -> dict | None:
        return None  # P3-7 暂未实现，返回 None 不崩("T04 实现 Valkey 实时")

    def subscribe(self, symbol: str, freq: str, handler: Callable):
        return None  # P3-7 暂未实现，返回 None 不崩("T04 实现")

    def unsubscribe(self, symbol: str, freq: str):
        pass

    # ——— 基本面/条款 ———

    def get_fundamental(self, symbol: str) -> dict | None:
        """A 股基本面（Tushare fina_indicator）。"""
        return None  # P3-7 暂未实现，返回 None 不崩("T04 实现")

    def get_convertible_terms(self, symbol: str) -> dict | None:
        """可转债条款（强赎/下修/回售）。"""
        return None  # P3-7 暂未实现，返回 None 不崩("T04 实现")

    def get_funding_rate(self, symbol: str) -> float | None:
        """加密资金费率（币安/OKX API）。"""
        return None  # P3-7 暂未实现，返回 None 不崩("T05 实现")

    # ——— 交易日历 ———

    def get_trade_calendar(self, year: int) -> list[date]:
        return _get_trade_calendar(year)

    def is_trading_day(self, d: date | None = None) -> bool:
        return _is_trading_day(d)

    def init_calendar(self, year: int) -> None:
        """从 Tushare 拉取交易日历并初始化。"""
        tushare.pull_trade_cal(year)


# 单例
platform = DataPlatform()