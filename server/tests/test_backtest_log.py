"""回测日志采集测试（ptrade 批 1）：run 作用域捕获 + 字段传播。"""
import logging
from datetime import datetime
from unittest.mock import patch


def test_run_log_handler_captures():
    """_RunLogHandler 把 run 作用域的日志追加到 logs（contextvar 隔离）。"""
    from src.strategy_framework.backtest import _RunLogHandler, _run_logs_ctx
    logs = []
    tok = _run_logs_ctx.set(logs)
    handler = _RunLogHandler()
    lg = logging.getLogger("backtest")
    lg.addHandler(handler)
    try:
        lg.warning("资金不足测试")
    finally:
        lg.removeHandler(handler)
        _run_logs_ctx.reset(tok)
    assert len(logs) == 1
    assert logs[0]["msg"] == "资金不足测试"
    assert logs[0]["level"] == "WARNING"


def test_run_result_has_logs_field():
    """run 后 BacktestResult.logs 字段存在（字段传播，即使空）。"""
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
