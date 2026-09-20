"""批 56a·M1 填充链写侧 SQL 钉（mock 连接——无库环境可跑；任务文件 mock 节承诺）。

钉什么：upsert_rows/upsert_state 必须 executemany 单批（18 号 §2.1——禁逐行 execute）
且含 ON CONFLICT 幂等子句 + 全列携带（品类值/生命周期列——盲审 A：server_default 品类错值）。
"""
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROW = ("600000.SHSE", "astock", "SHSE", "stock", "浦发银行", None,
        100, 0.01, "T+1", "19991110", "")


def _mock_conn():
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    cur = MagicMock()
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    conn.cursor.return_value = cur
    return conn, cur


class TestUpsertRows:
    def test_executemany_on_conflict_full_columns(self):
        import src.data_platform.db as dbm
        conn, cur = _mock_conn()
        with patch.object(dbm, "get_conn", return_value=conn):
            from src.data_platform.security_master import SMClient
            n = SMClient().upsert_rows([_ROW])
        assert n == 1
        cur.executemany.assert_called_once()
        sql, params = cur.executemany.call_args[0]
        assert "ON CONFLICT (vt_symbol) DO UPDATE" in sql
        assert "multiplier=EXCLUDED.multiplier" in sql        # 品类值随行（盲审 A）
        assert "delist_date=EXCLUDED.delist_date" in sql      # 生命周期列字段级更新（29 §四）
        assert "list_date=COALESCE(EXCLUDED.list_date" in sql  # 空值不抹旧
        assert len(params[0]) == 11                            # 全列携带
        assert params[0][9] == "19991110" and params[0][10] is None  # 脏空串→NULL 清洗

    def test_dirty_date_nullified(self):
        import src.data_platform.db as dbm
        conn, cur = _mock_conn()
        dirty = ("113531.SHSE", "astock", "SHSE", "convertible", "x", None,
                 10, 0.001, "T+0", "None", "garbage")
        with patch.object(dbm, "get_conn", return_value=conn):
            from src.data_platform.security_master import SMClient
            SMClient().upsert_rows([dirty])
        params = cur.executemany.call_args[0][1]
        assert params[0][9] is None and params[0][10] is None  # 'None'/'garbage'→NULL


class TestUpsertState:
    def test_executemany_on_conflict(self):
        import src.data_platform.db as dbm
        conn, cur = _mock_conn()
        with patch.object(dbm, "get_conn", return_value=conn):
            from src.data_platform.security_master import SMClient
            n = SMClient().upsert_state([("600000.SHSE", "2026-01-01", "st", '{"is_st": false}')])
        assert n == 1
        cur.executemany.assert_called_once()
        sql = cur.executemany.call_args[0][0]
        assert "ON CONFLICT (vt_symbol, effective_from, kind)" in sql
