"""B1 on_bar 回调单测（#18）：BacktestEngine.run 每bar 后调 callback（progress/equity/trades）。

mock precheck/validate（避免因子窗口/品类干扰 B1 回调机制测试）。
"""
from datetime import datetime
from unittest.mock import patch


def test_on_bar_callback():
    """run 每bar 后调 on_bar_callback，progress.current 1..N，pct 0..100。"""
    from src.strategy_framework.backtest import BacktestEngine
    from src.strategy_framework.strategy import StrategyConfig

    engine = BacktestEngine(initial_capital=100000)
    cfg = StrategyConfig(
        id="t", name="t", type="astock_analysis", symbol="600000.SHSE",
        adapter="xtp", factors=[{"name": "ma_dev", "weight": 1, "params": {"n": 5}}],
        aggregator={"threshold_buy": 0.0, "threshold_sell": 0.0},
    )
    bars = [
        {"ts": datetime(2026, 1, i + 1), "open": 10.0, "high": 11.0, "low": 9.0,
         "close": 10.0 + i * 0.1, "volume": 100.0}
        for i in range(10)
    ]
    callbacks = []

    def cb(bar, ctx):
        callbacks.append({"current": ctx["progress"]["current"], "pct": ctx["progress"]["pct"],
                          "equity": ctx["equity"], "has_trades": "trades" in ctx})

    with patch("src.strategy_framework.backtest.precheck_backtest_data",
               return_value={"valid": True, "issues": [], "checks": [], "bars_count": 10, "max_window": 5}), \
         patch("src.strategy_framework.backtest.validate_no_future_data",
               return_value={"valid": True, "checks": [], "warnings": []}):
        engine.run(cfg, bars, on_bar_callback=cb)

    assert len(callbacks) == 10                  # 10 bar 调 10 次
    assert callbacks[0]["current"] == 1
    assert callbacks[-1]["current"] == 10
    assert callbacks[-1]["pct"] == 100.0
    assert "equity" in callbacks[0]
    assert callbacks[0]["has_trades"] is True     # ctx 含 trades
