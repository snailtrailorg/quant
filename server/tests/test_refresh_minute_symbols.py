"""refresh_minute_symbols 展开表同步测试（分钟数据源重构 21 号 §3.1）。"""
from unittest.mock import MagicMock, patch


def _conn(rows=None, count=0):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = rows or []
    conn.execute.return_value.fetchone.return_value = (count,)
    return conn


def test_refresh_calls_delete_insert_count():
    """refresh 幂等重算：DELETE pool + INSERT 展开（DISTINCT ON + ON CONFLICT DO NOTHING）+ 返回 count。"""
    from src.data_platform import db
    conn = _conn(count=3)
    with patch.object(db, "get_conn", return_value=conn):
        n = db.refresh_minute_symbols()
    assert n == 3
    sqls = [c.args[0] for c in conn.execute.call_args_list]
    assert any("DELETE FROM minute_symbols WHERE source LIKE 'pool:%'" in s for s in sqls)
    assert any("INSERT INTO minute_symbols" in s and "DISTINCT ON" in s
               and "ON CONFLICT (symbol) DO NOTHING" in s for s in sqls)
    assert any("SELECT count(*) FROM minute_symbols" in s for s in sqls)
    conn.commit.assert_called_once()


def test_refresh_insert_filters_astock_and_mhs():
    """INSERT 只展开 category=astock 且 minute_history_start 非空的池成员。"""
    from src.data_platform import db
    conn = _conn()
    with patch.object(db, "get_conn", return_value=conn):
        db.refresh_minute_symbols()
    insert_sql = next(c.args[0] for c in conn.execute.call_args_list
                      if "INSERT INTO minute_symbols" in c.args[0])
    assert "p.minute_history_start IS NOT NULL" in insert_sql
    assert "p.category = 'astock'" in insert_sql
