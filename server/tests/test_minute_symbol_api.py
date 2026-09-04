"""minute-symbol 三端点测试（分钟数据源重构 21 号 §3.5）。"""
from unittest.mock import MagicMock, patch

import pytest


def _conn(rows=None):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = rows or []
    return conn


def test_list_minute_symbols():
    """GET 展开表：返回 symbol/source/last_ts（空 last_ts → None）。"""
    from src.web_api.routes import backtest as b
    conn = _conn([("600000.SHSE", "direct", "2026-09-04 15:01:00"),
                  ("000001.SZSE", "pool:3", "")])
    with patch.object(b, "get_conn", return_value=conn):
        r = b.list_minute_symbols({})
    assert r["symbols"] == [
        {"symbol": "600000.SHSE", "source": "direct", "last_ts": "2026-09-04 15:01:00"},
        {"symbol": "000001.SZSE", "source": "pool:3", "last_ts": None},
    ]


def test_add_minute_symbol_normalizes_vt():
    """POST 直标：ts 格式归一 vt，UPSERT source='direct'。"""
    from src.web_api.routes import backtest as b
    conn = _conn()
    with patch.object(b, "get_conn", return_value=conn), \
         patch.object(b, "audit_log"):
        r = b.add_minute_symbol("600000.SH", {"username": "test"})
    assert r == {"status": "added", "symbol": "600000.SHSE"}
    sql = conn.execute.call_args.args[0]
    assert "ON CONFLICT (symbol) DO UPDATE SET source='direct'" in sql


def test_add_minute_symbol_invalid():
    """POST 直标：无后缀符号拒绝。"""
    from src.web_api.errors import ApiError
    from src.web_api.routes import backtest as b
    with pytest.raises(ApiError) as e:
        b.add_minute_symbol("600000", {"username": "test"})
    assert e.value.code == "SYMBOL_INVALID"


def test_del_minute_symbol_direct_only():
    """DELETE 直标：只删 source='direct'（池驱动 pool 行不删）。"""
    from src.web_api.routes import backtest as b
    conn = _conn()
    with patch.object(b, "get_conn", return_value=conn), \
         patch.object(b, "audit_log"):
        r = b.del_minute_symbol("600000.SHSE", {"username": "test"})
    assert r == {"status": "removed", "symbol": "600000.SHSE"}
    sql = conn.execute.call_args.args[0]
    assert "source='direct'" in sql
