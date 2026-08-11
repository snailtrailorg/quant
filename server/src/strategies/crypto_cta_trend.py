"""加密永续合约 CTA 趋势策略（P1-8）。

双均线交叉（快线上穿慢线 BUY / 下穿 SELL），适用于 BTC/ETH 4H/1H。
继承 Strategy + @register_strategy('crypto_perp')。
外部 gate：币安/OKX API 未开通，回测用 PG bar，实盘待接网关。
"""

from __future__ import annotations
from src.strategy_framework.strategy import Strategy, Signal, Action, register_strategy


@register_strategy("crypto_perp")
class CryptoCTATrendStrategy(Strategy):
    """加密 CTA 趋势策略（双均线交叉，P1-8）。"""

    def on_bar(self, bar: dict, history: list[dict] | None = None) -> Signal | None:
        """双均线交叉：快线（SMA10）上穿慢线（SMA30）BUY / 下穿 SELL。"""
        from src.strategy_framework import BarContext
        history = history or []
        ctx = BarContext(
            close=bar.get("close", 0), high=bar.get("high", 0),
            low=bar.get("low", 0), open_=bar.get("open", 0),
            volume=bar.get("volume", 0),
        )

        fast_n = self.config.params.get("fast_n", 10)
        slow_n = self.config.params.get("slow_n", 30)

        closes = [h.get("close", 0) for h in history] + [ctx.close]
        if len(closes) < slow_n + 1:
            return Signal(action=Action.HOLD, reason=f"数据不足（{len(closes)}/{slow_n+1}）")

        sma_fast_now = sum(closes[-fast_n:]) / fast_n
        sma_slow_now = sum(closes[-slow_n:]) / slow_n
        # prev = 不含当前 bar 的前 N 根
        sma_fast_prev = sum(closes[-fast_n-1:-1]) / fast_n if len(closes) > fast_n else sma_fast_now
        sma_slow_prev = sum(closes[-slow_n-1:-1]) / slow_n if len(closes) > slow_n else sma_slow_now

        # 金叉（快线上穿慢线）
        if sma_fast_prev <= sma_slow_prev and sma_fast_now > sma_slow_now:
            sig = Signal(action=Action.BUY, score=1.0, symbol=self.symbol,
                        volume=self.config.params.get("shares_per_trade", 1),
                        price=ctx.close, reason=f"金叉: fast {sma_fast_now:.2f} > slow {sma_slow_now:.2f}")
            self.place_order(sig)
            return sig
        # 死叉（快线下穿慢线）
        elif sma_fast_prev >= sma_slow_prev and sma_fast_now < sma_slow_now:
            sig = Signal(action=Action.SELL, score=-1.0, symbol=self.symbol,
                        volume=self.config.params.get("shares_per_trade", 1),
                        price=ctx.close, reason=f"死叉: fast {sma_fast_now:.2f} < slow {sma_slow_now:.2f}")
            self.place_order(sig)
            return sig
        return Signal(action=Action.HOLD, reason=f"fast {sma_fast_now:.2f} vs slow {sma_slow_now:.2f}")
