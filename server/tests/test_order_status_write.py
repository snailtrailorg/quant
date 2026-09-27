"""批 68：order_log 终态回写测试钉（映射/只进不退/vt 两步/send_failed 可覆写/risk 语义）。"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from vnpy.trader.constant import Status


def _d(status, vt="EMT.123"):
    return SimpleNamespace(vt_orderid=vt, status=status, symbol="600000.SHSE")


def _conn(fetch_rows):
    """fetch_rows: list——每次 fetchone 依次返回（None 含）。"""
    conn = MagicMock(); conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.fetchone.side_effect = list(fetch_rows)
    cur.rowcount = 1
    conn.execute.return_value = cur
    return conn, cur


class TestWriteOrderStatus:
    def test_mapping_and_skip(self):
        from src.strategy_runner.trading import write_order_status
        # NOTTRADED/SUBMITTING 不回写（不触 DB）
        with patch("src.data_platform.db.get_conn") as g:
            write_order_status(_d(Status.NOTTRADED), MagicMock(), "s1", "X")
            write_order_status(_d(Status.SUBMITTING), MagicMock(), "s1", "X")
        g.assert_not_called()
        # ALLTRADED → all_traded
        conn, cur = _conn([(41,)])
        with patch("src.data_platform.db.get_conn", return_value=conn):
            write_order_status(_d(Status.ALLTRADED), MagicMock(), "s1", "X")
        upd = [c for c in conn.execute.call_args_list if "UPDATE order_log" in c.args[0]][0]
        assert upd.args[1][0] == "all_traded"

    def test_vt_two_step_latest_row(self):
        """vt 非唯一：SELECT LIMIT 1 最新再 UPDATE WHERE id（多行不击中）。"""
        from src.strategy_runner.trading import write_order_status
        conn, cur = _conn([(77,)])
        with patch("src.data_platform.db.get_conn", return_value=conn):
            write_order_status(_d(Status.CANCELLED), MagicMock(), "s1", "X")
        sel = conn.execute.call_args_list[0]
        assert "ORDER BY id DESC LIMIT 1" in sel.args[0]
        upd = conn.execute.call_args_list[1]
        assert "WHERE id=%s" in upd.args[0] and upd.args[1][1] == 77

    def test_cid_fallback_with_lock(self):
        """vt 空/无行 → _lock 下 _vt2cid 反查 client_order_id。"""
        from src.strategy_runner.trading import write_order_status
        adapter = MagicMock()
        adapter._vt2cid.get.return_value = "cid-9"
        adapter._lock = MagicMock()
        adapter._lock.__enter__ = MagicMock(return_value=None)
        adapter._lock.__exit__ = MagicMock(return_value=False)
        conn, _ = _conn([None, (55,)])
        with patch("src.data_platform.db.get_conn", return_value=conn):
            write_order_status(_d(Status.REJECTED, vt=""), adapter, "s1", "X")
        adapter._vt2cid.get.assert_called()
        sel2 = conn.execute.call_args_list[0]   # vt 空=跳过 vt 步，首个 execute 即 cid 查询
        assert "client_order_id" in sel2.args[0]

    def test_final_state_guards_update(self):
        """只进不退：UPDATE WHERE status NOT IN 终态集（迟到 partial 片段被 SQL 闭包挡）。"""
        from src.strategy_runner.trading import ORDER_FINAL_STATES, write_order_status
        conn, _ = _conn([(9,)])
        with patch("src.data_platform.db.get_conn", return_value=conn):
            write_order_status(_d(Status.PARTTRADED), MagicMock(), "s1", "X")
        upd = conn.execute.call_args_list[1]
        assert "NOT IN" in upd.args[0]
        assert "partial" in upd.args[1]

    def test_never_raise(self):
        from src.strategy_runner.trading import write_order_status
        with patch("src.data_platform.db.get_conn", side_effect=RuntimeError("db down")):
            write_order_status(_d(Status.ALLTRADED), MagicMock(), "s1", "X")   # 不抛即过


class TestRiskCountSemantics:
    def test_sql_immune_to_status_progression(self):
        """P0 钉：日频次计数=已下委托数（status != send_failed——回写推进不出计数）。"""
        import inspect
        from src.risk_control import risk
        src = inspect.getsource(risk)
        assert "status != 'send_failed'" in src
        assert "part_filled" not in src   # 旧占位字面量退役
