"""可转债双低轮动策略。

双低 = 价格 + 溢价率 × 100。定期轮动，选双低值最小的 N 只持有。
"""

from __future__ import annotations
from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import Any

from src.strategy_framework import Strategy, StrategyConfig, Signal, Action, SignalAggregator
from src.strategy_framework import create_adapter, list_factors, register_factor, BarContext
from src.data_platform import platform


@dataclass
class DoubleLowConfig:
    """双低策略配置参数。"""
    top_n: int = 10                 # 选前 N 只
    rebalance_days: int = 5         # 每 N 天轮动一次
    min_price: float = 80.0         # 最低价格过滤
    max_price: float = 150.0        # 最高价格过滤
    min_volume: float = 0           # 最低日均成交额(万元)，0=不限
    premium_weight: float = 1.0     # 溢价率权重（1.0 = 标准双低）


class ConvertibleDoubleLowStrategy:
    """可转债双低轮动策略。

    用法:
        strategy = ConvertibleDoubleLowStrategy(...)
        result = strategy.backtest(start_date="20260701", end_date="20260722")
    """

    def __init__(self, config: DoubleLowConfig | None = None):
        self.config = config or DoubleLowConfig()
        self._last_rebalance: date | None = None
        self._holdings: list[str] = []  # 当前持仓 vt_symbol 列表

    # ——— 回测核心 ———

    def backtest(self, start_date: str, end_date: str) -> dict:
        """运行回测。"""
        from datetime import datetime
        from src.data_platform.adapters.tushare_adapter import pull_cb_daily, to_save_rows
        from src.data_platform.db import save_bars, get_bars
        from src.data_platform import to_vt_symbol
        import pandas as pd

        start = datetime.strptime(start_date, "%Y%m%d").date()
        end = datetime.strptime(end_date, "%Y%m%d").date()

        # 1. 拉全量可转债日线
        print(f"拉取可转债日线 {start_date}-{end_date} ...")
        raw = pull_cb_daily(start_date, end_date)
        if raw.empty:
            return {"error": "无可转债数据"}

        # 2. 转 vt_symbol + 存 PG
        for _, row in raw.iterrows():
            vt = to_vt_symbol(row["ts_code"])
            # 简化：直接存全量

        # 3. 按日期分组，逐日模拟
        dates = sorted(raw["trade_date"].unique())
        portfolio_log = []
        cash = 1_000_000  # 初始资金
        position = 0
        total_value = cash

        for i, trade_date in enumerate(dates):
            d = pd.Timestamp(trade_date).to_pydatetime().date()
            day_data = raw[raw["trade_date"] == trade_date].copy()

            # 过滤
            day_data = day_data[
                (day_data["close"] >= self.config.min_price) &
                (day_data["close"] <= self.config.max_price) &
                (day_data["amount"] >= self.config.min_volume)
            ].copy()

            if day_data.empty:
                portfolio_log.append({"date": trade_date, "holdings": 0, "value": total_value})
                continue

            # 计算双低得分 = close + premium_weight × premium_rate
            # 简化为只用价格（待 premium_rate 数据源接入后补充）
            day_data["double_low"] = day_data["close"]

            # 选前 N 只
            day_data = day_data.nsmallest(self.config.top_n, "double_low")

            # 轮动
            if self._should_rebalance(d, i):
                selected = day_data.head(self.config.top_n)
                position = len(selected)
                avg_price = selected["close"].mean()
                total_value = position * avg_price * 10  # 每手 10 张简化

            portfolio_log.append({
                "date": trade_date,
                "holdings": position,
                "value": total_value,
                "top_picks": day_data.head(5)["ts_code"].tolist(),
            })

        # 4. 计算业绩
        returns = pd.DataFrame(portfolio_log)
        total_return = (returns["value"].iloc[-1] / 1_000_000 - 1) * 100 if len(returns) > 1 else 0
        max_drawdown = 0.0
        peak = 0
        for v in returns["value"]:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_drawdown:
                max_drawdown = dd

        return {
            "start_date": start_date,
            "end_date": end_date,
            "total_return_pct": round(total_return, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "total_days": len(portfolio_log),
            "final_value": round(returns["value"].iloc[-1], 2),
            "top_n": self.config.top_n,
        }

    def _should_rebalance(self, d: date, idx: int) -> bool:
        """判断是否轮动。"""
        if self._last_rebalance is None:
            self._last_rebalance = d
            return True
        if (d - self._last_rebalance).days >= self.config.rebalance_days:
            self._last_rebalance = d
            return True
        return False