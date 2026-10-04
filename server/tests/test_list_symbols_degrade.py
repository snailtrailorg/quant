"""批 91：list_symbols 本地计数聚合失败必须降级（此前超时异常逃逸 ⇒ 整页 500）。

反证：删掉 engine.list_symbols 里 `except Exception` 的降级分支，本用例因异常上抛而红。
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import psycopg

import src.data_sync.engine as eng


@contextmanager
def _cm(conn):
    yield conn


def _cursor(rows=None, one=None):
    c = MagicMock()
    c.fetchall.return_value = list(rows or [])
    c.fetchone.return_value = one
    return c


def _sym_conn(rows):
    """asset_static_info 两次查询：明细(rows) + count(1)。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.transaction.return_value.__enter__.return_value = conn
    conn.transaction.return_value.__exit__.return_value = False

    def _exec(sql, params=None):
        return _cursor(one=(len(rows),)) if "count(*)" in sql else _cursor(rows=rows)

    conn.execute.side_effect = _exec
    return conn


def _patches(conn_sym, conn_heavy):
    return (
        patch.object(eng, "_get_pro_api", return_value=(MagicMock(), "astock", "1D", "daily")),
        patch.object(eng, "get_conn", return_value=conn_sym),
        patch.object(eng, "get_conn_for_heavy_read", lambda *a, **k: _cm(conn_heavy)),
    )


def test_aggregate_timeout_degrades_to_count_error():
    rows = [("600000.SH", "浦发银行", "19991110")]
    heavy = MagicMock()
    heavy.execute.side_effect = psycopg.errors.QueryCanceled(
        "canceling statement due to statement timeout")

    p1, p2, p3 = _patches(_sym_conn(rows), heavy)
    with p1, p2, p3:
        out = eng.list_symbols("astock_daily", size=10)

    assert out["total"] == 1
    assert len(out["items"]) == 1                       # 清单仍可见（不再 500）
    assert out["items"][0]["local_count"] == 0          # 降级：计数不可信
    assert "count_error" in out and "QueryCanceled" in out["count_error"]


def test_aggregate_success_no_flag():
    rows = [("600000.SH", "浦发银行", "19991110")]
    ts_lo = datetime(2026, 9, 28, 7, 0, tzinfo=timezone.utc)
    ts_hi = datetime(2026, 9, 30, 7, 0, tzinfo=timezone.utc)
    heavy = MagicMock()
    heavy.execute.return_value = _cursor(rows=[("600000.SHSE", 3, ts_lo, ts_hi)])

    p1, p2, p3 = _patches(_sym_conn(rows), heavy)
    with p1, p2, p3:
        out = eng.list_symbols("astock_daily", size=10)

    assert "count_error" not in out                     # 成功路径不带标记（契约不变）
    assert out["items"][0]["local_count"] == 3
    assert out["items"][0]["local_first"] == "20260928"
    assert out["items"][0]["local_last"] == "20260930"
