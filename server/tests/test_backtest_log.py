"""回测日志采集测试（ptrade 批 1）：显式收集器（Strategy._log_fn + 引擎侧补记进 run_logs）。"""
from datetime import datetime
from unittest.mock import patch


def test_strategy_log_uses_injected_log_fn():
    """Strategy.log 注入 _log_fn 后走收集器，不 fallback 模块 logger。"""
    from src.strategy_framework.strategy import Strategy, StrategyConfig
    cfg = StrategyConfig(id="t", name="t", type="astock_analysis",
                         symbol="600000.SHSE", adapter="xtp",
                         factors=[{"name": "ma_dev", "weight": 1, "params": {"n": 5}}],
                         aggregator={"threshold_buy": 0.0, "threshold_sell": 0.0})
    s = Strategy(cfg, None)
    collected = []
    s._log_fn = lambda msg, level: collected.append((msg, level))
    s.log("测试策略日志", "info")
    assert collected == [("测试策略日志", "info")]


def test_run_result_has_logs_field():
    """run 后 BacktestResult.logs 字段存在（显式收集器传播）。"""
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
    with patch("src.strategy_framework.backtest.precheck_backtest_data",
               return_value={"valid": True, "issues": [], "checks": [], "bars_count": 10, "max_window": 5}), \
         patch("src.strategy_framework.backtest.validate_no_future_data",
               return_value={"valid": True, "checks": [], "warnings": []}):
        result = engine.run(cfg, bars)
    assert isinstance(result.logs, list)


def test_engine_insufficient_funds_logged():
    """引擎侧补记（资金不足）进 result.logs——显式收集，无全局 logger 依赖。"""
    from src.strategy_framework.backtest import BacktestEngine
    from src.strategy_framework.strategy import StrategyConfig
    engine = BacktestEngine(initial_capital=100)   # 现金极低，必资金不足
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
    with patch("src.strategy_framework.backtest.precheck_backtest_data",
               return_value={"valid": True, "issues": [], "checks": [], "bars_count": 10, "max_window": 5}), \
         patch("src.strategy_framework.backtest.validate_no_future_data",
               return_value={"valid": True, "checks": [], "warnings": []}):
        result = engine.run(cfg, bars, shares_per_trade=1000)
    # 1000股 @ ~10元 = ~10000 > 现金 100 → 资金不足，引擎补记日志
    assert any("资金不足" in l["msg"] for l in result.logs), result.logs
