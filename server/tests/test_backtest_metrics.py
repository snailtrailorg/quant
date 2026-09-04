"""回测 metrics 端点测试（ptrade 批 2）：滚动绩效按 type 返回 + 多标的均值聚合。"""
import json
from unittest.mock import MagicMock, patch

import pytest


def _conn_with_results(results):
    """mock get_conn，返回 backtest_symbols 的 (symbol, result) 行。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [
        (sym, json.dumps(res)) for sym, res in results
    ]
    return conn


def test_backtest_metrics_by_type_aggregates():
    """按 type 返回滚动绩效，多标的均值聚合，窗口不足 None。"""
    from src.web_api.routes import backtest as b
    rolling1 = {"2026-01": {"1": {"return": 1.0, "alpha": 0.1}, "3": None},
                "2026-02": {"1": {"return": 2.0, "alpha": 0.2}, "3": {"return": 3.0, "alpha": 0.3}}}
    rolling2 = {"2026-01": {"1": {"return": 3.0, "alpha": 0.5}, "3": None},
                "2026-02": {"1": {"return": 4.0, "alpha": 0.6}, "3": {"return": 5.0, "alpha": 0.7}}}
    conn = _conn_with_results([("600000.SHSE", {"metrics": {"rolling": rolling1}}),
                               ("000001.SZSE", {"metrics": {"rolling": rolling2}})])
    with patch.object(b, "get_conn", return_value=conn):
        r = b.backtest_metrics(1, "return", {})
    assert r["data"]["2026-01"]["1"] == 2.0   # (1.0+3.0)/2
    assert r["data"]["2026-01"]["3"] is None
    assert r["data"]["2026-02"]["1"] == 3.0   # (2.0+4.0)/2
    assert r["data"]["2026-02"]["3"] == 4.0   # (3.0+5.0)/2


def test_backtest_metrics_alpha_type():
    """type=alpha 返回 alpha 键，非 return。"""
    from src.web_api.routes import backtest as b
    rolling = {"2026-01": {"1": {"return": 1.0, "alpha": 0.15}}}
    conn = _conn_with_results([("600000.SHSE", {"metrics": {"rolling": rolling}})])
    with patch.object(b, "get_conn", return_value=conn):
        r = b.backtest_metrics(1, "alpha", {})
    assert r["data"]["2026-01"]["1"] == 0.15


def test_backtest_metrics_invalid_type():
    """非法 type 返回 400 INVALID_METRIC_TYPE。"""
    from src.web_api.routes import backtest as b
    from src.web_api.errors import ApiError
    with patch.object(b, "get_conn"):
        with pytest.raises(ApiError) as e:
            b.backtest_metrics(1, "garbage", {})
    assert e.value.code == "INVALID_METRIC_TYPE"
