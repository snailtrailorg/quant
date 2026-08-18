"""可转债双低轮动策略（P1-2 接框架）。

双低 = price + premium_rate × 100。继承 Strategy + @register_strategy 注册，
from_config(type='convertible_t0') 分发到此。
on_bar 用 double_low 因子（价格低+溢价率低）打分，低分买入轮动。
"""

from __future__ import annotations
from src.strategy_framework.strategy import Strategy, Signal, Action, register_strategy
from src.strategy_framework.factor import get_factor


@register_strategy("convertible_t0")
class ConvertibleDoubleLowStrategy(Strategy):
    """可转债双低轮动策略（接框架版，P1-2）。

    与基类 Strategy 一致走 on_bar → 因子 → 信号 → place_order，
    配置 double_low 因子 + 低阈值买入。
    """

    def on_bar(self, bar: dict, history: list[dict] | None = None) -> Signal | None:
        """双低策略 on_bar：用 double_low 因子（或简化价格）打分。"""
        from src.strategy_framework import BarContext
        ctx = BarContext(
            close=bar.get("close", 0), high=bar.get("high", 0),
            low=bar.get("low", 0), open_=bar.get("open", 0),
            volume=bar.get("volume", 0),
        )
        factor_values = self.compute_factors(ctx)

        # double_low 因子返回负价格（低价格好）。信号聚合后 BUY。
        sig = self._aggregator.aggregate(factor_values)
        if sig and sig.action != Action.HOLD:
            sig.symbol = self.symbol
            # R-S3a：price 回填（此前恒 0 → SC3 丢弃 → 实盘零下单）+ 执行规则接线（同基类）
            sig.price = sig.price or bar.get("close", 0)
            sig.volume = sig.volume or self.config.params.get("shares_per_trade", 100)
            sig.price_type = self.config.params.get("price_type", "LIMIT")
            sig.order_validity = self.config.params.get("order_validity", "DAY")
            self.place_order(sig)
        return sig
