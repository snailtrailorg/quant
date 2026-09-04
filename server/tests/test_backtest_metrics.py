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


def test_backtest_export_generates_xlsx():
    """导出 Excel：返回 xlsx 文件流（指标概览/交易/持仓三 sheet）。"""
    from src.web_api.routes import backtest as b
    result = {"total_return_pct": 10.5,
              "trades": [{"ts": "2026-01-01", "action": "BUY", "volume": 100, "price": 10, "commission": 0.5}],
              "daily_values": [{"ts": "2026-01-01", "close": 10, "position": 100, "avg_price": 10, "cash": 9000, "value": 10000}]}
    conn = _conn_with_results([("600000.SHSE", result)])
    with patch.object(b, "get_conn", return_value=conn):
        resp = b.backtest_export(1, "600000.SHSE", {})
    assert resp.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "backtest_1.xlsx" in resp.headers["Content-Disposition"]


def test_backtest_export_pdf_generates():
    """导出 PDF：返回 pdf 文件流（weasyprint HTML→PDF，系统字体 fc 自动发现；lang 回落）。"""
    from src.web_api.routes import backtest as b
    result = {"total_return_pct": 10.5,
              "trades": [{"ts": "2026-01-01", "action": "BUY", "volume": 100, "price": 10, "commission": 0.5}],
              "daily_values": [{"ts": "2026-01-01", "close": 10, "position": 100, "avg_price": 10, "cash": 9000, "value": 10000}]}
    conn = _conn_with_results([("600000.SHSE", result)])
    fake_html = MagicMock()
    with patch.object(b, "get_conn", return_value=conn), \
         patch("weasyprint.HTML", fake_html):
        resp = b.backtest_export_pdf(1, "600000.SHSE", "zh", {})
    assert resp.media_type == "application/pdf"
    assert "backtest_1.pdf" in resp.headers["Content-Disposition"]
    html_str = fake_html.call_args.kwargs.get("string") or fake_html.call_args.args[0]
    assert "标的" in html_str          # zh 列头渲染
    assert "600000.SHSE" in html_str   # 数据经 _esc 入模板
