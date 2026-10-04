"""批 91：/api/data-integrity 失败必须可见（此前静默返空 ⇒ 界面谎报「0 只标的」）。

反证：删掉 risk.data_integrity_api 的 except 分支里的 logger.exception / "error" 键，
test_failure_returns_error_field 立即变红。
"""
from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from src.web_api.routes import risk as risk_mod


@contextmanager
def _cm(conn):
    yield conn


def _cur(rows):
    c = MagicMock()
    c.fetchall.return_value = list(rows)
    return c


def _conn(rows=None, cal=None):
    conn = MagicMock()
    conn.execute.side_effect = lambda sql, params=None: (
        _cur(cal or []) if "trade_cal" in sql else _cur(rows or [])
    )
    return conn


@pytest.fixture(autouse=True)
def _clear_agg_cache():
    """_AGG_CACHE 是进程级 60s 缓存——不隔离则用例互相污染（先失败后成功会读到失败体）。"""
    risk_mod._AGG_CACHE.clear()
    yield
    risk_mod._AGG_CACHE.clear()


def test_failure_returns_error_field_and_logs():
    conn = MagicMock()
    conn.execute.side_effect = psycopg.errors.QueryCanceled(
        "canceling statement due to statement timeout")

    with patch.object(risk_mod, "get_conn_for_heavy_read", lambda *a, **k: _cm(conn)), \
            patch.object(risk_mod, "logger") as lg:
        out = risk_mod.data_integrity_api(freq="1D", payload={"username": "t"})

    # 失败体必须与「表真的空」可区分——否则前端错误条永不亮
    assert out["items"] == []
    assert out["summary"]["total"] == 0
    assert "error" in out and "QueryCanceled" in out["error"]
    assert lg.exception.called


def test_success_shape_unchanged():
    rows = [("600000.SHSE", 3, date(2026, 9, 28), date(2026, 9, 30))]
    cal = [(date(2026, 9, 28),), (date(2026, 9, 29),), (date(2026, 9, 30),)]
    with patch.object(risk_mod, "get_conn_for_heavy_read", lambda *a, **k: _cm(_conn(rows, cal))):
        out = risk_mod.data_integrity_api(freq="1D", payload={"username": "t"})

    assert "error" not in out          # 成功路径不带 error 键（契约不变）
    assert out["summary"] == {"total": 1, "complete": 1, "partial": 0, "missing": 0}
    assert out["items"][0]["symbol"] == "600000.SHSE"
    assert out["items"][0]["pct"] == 100.0
    assert out["items"][0]["status"] == "complete"


def test_bad_freq_still_errors():
    out = risk_mod.data_integrity_api(freq="7D", payload={"username": "t"})
    assert "error" in out
