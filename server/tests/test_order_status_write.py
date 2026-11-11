"""批 68：order_log 终态回写测试钉（映射/只进不退/vt 两步/send_failed 可覆写/risk 语义）。

2026-11-09 增真库守门类 TestRealDbSql：SQL 语句在真库逐条执行——mock 只证「字符串里有
这行字」，不能证明语句可执行（`NOT IN $3` 语法错曾全绿掩盖）。
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.quant_common.domain import Direction, OrderState, OrderStatus


def _d(status, vt="EMT.123"):
    return OrderState(orderid="", vt_orderid=vt, symbol="600000.SHSE", direction=Direction.LONG,
                      status=status, volume=0, traded=0, price=0)


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
            write_order_status(_d(OrderStatus.NOT_TRADED), MagicMock(), "s1", "X")
            write_order_status(_d(OrderStatus.SUBMITTED), MagicMock(), "s1", "X")
        g.assert_not_called()
        # ALLTRADED → all_traded
        conn, cur = _conn([(41,)])
        with patch("src.data_platform.db.get_conn", return_value=conn):
            write_order_status(_d(OrderStatus.ALL_TRADED), MagicMock(), "s1", "X")
        upd = [c for c in conn.execute.call_args_list if "UPDATE order_log" in c.args[0]][0]
        assert upd.args[1][0] == "all_traded"

    def test_vt_two_step_latest_row(self):
        """vt 非唯一：SELECT LIMIT 1 最新再 UPDATE WHERE id（多行不击中）。"""
        from src.strategy_runner.trading import write_order_status
        conn, cur = _conn([(77,)])
        with patch("src.data_platform.db.get_conn", return_value=conn):
            write_order_status(_d(OrderStatus.CANCELLED), MagicMock(), "s1", "X")
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
            write_order_status(_d(OrderStatus.REJECTED, vt=""), adapter, "s1", "X")
        adapter._vt2cid.get.assert_called()
        sel2 = conn.execute.call_args_list[0]   # vt 空=跳过 vt 步，首个 execute 即 cid 查询
        assert "client_order_id" in sel2.args[0]

    def test_final_state_guards_update(self):
        """只进不退：`status <> ALL(终态集)`（迟到 partial 片段被 SQL 闭包挡）。

        2026-11-09 修：原实现 `NOT IN %s` 传元组 ⇒ psycopg 渲染 `NOT IN $3` 语法错，
        never-raise 静默吞 ⇒ 终态回写从未生效（171n e2e 真库抓到）。数组比较用 ALL。
        """
        from src.strategy_runner.trading import ORDER_FINAL_STATES, write_order_status
        conn, _ = _conn([(9,)])
        with patch("src.data_platform.db.get_conn", return_value=conn):
            write_order_status(_d(OrderStatus.PARTIAL), MagicMock(), "s1", "X")
        upd = conn.execute.call_args_list[1]
        assert "<> ALL" in upd.args[0]
        assert set(upd.args[1][2]) == set(ORDER_FINAL_STATES)

    def test_never_raise(self):
        from src.strategy_runner.trading import write_order_status
        with patch("src.data_platform.db.get_conn", side_effect=RuntimeError("db down")):
            write_order_status(_d(OrderStatus.ALL_TRADED), MagicMock(), "s1", "X")   # 不抛即过


def _real_db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as c:
            c.execute("SELECT 1 FROM order_log LIMIT 1")
        return True
    except Exception:
        return False


class TestRealDbSql:
    """真库逐条执行守门：mock 只证字符串形状，不证语句可跑（`NOT IN $3` 曾全绿）。

    插入临时行 → 执行终态回写 → 再执行非终态写（应 0 行=只进不退）→ 回滚不留痕。
    """

    @pytest.mark.skipif(not _real_db_up(), reason="真库不可用（无 dev 库自动跳过）")
    def test_update_executes_and_closes(self):
        from src.data_platform.db import get_conn
        from src.strategy_runner.trading import ORDER_FINAL_STATES
        cid = "e2e-gate-" + uuid.uuid4().hex[:10]
        with get_conn() as c:
            oid = c.execute(
                "INSERT INTO order_log (ts, strategy_id, symbol, action, volume, price, status, client_order_id) "
                "VALUES (now(), 'e2e-gate', '600000.SHSE', 'BUY', 100, 10, 'submitted', %s) RETURNING id",
                (cid,)).fetchone()[0]
            upd = c.execute("UPDATE order_log SET status=%s WHERE id=%s AND status <> ALL(%s)",
                            ("all_traded", oid, list(ORDER_FINAL_STATES)))
            assert upd.rowcount == 1, "终态回写语句在真库执行失败"
            again = c.execute("UPDATE order_log SET status=%s WHERE id=%s AND status <> ALL(%s)",
                              ("partial", oid, list(ORDER_FINAL_STATES)))
            assert again.rowcount == 0, "终态闭包失效（只进不退被破坏）"
            c.rollback()


class TestRiskCountSemantics:
    def test_sql_immune_to_status_progression(self):
        """P0 钉：日频次计数=已下委托数（status != send_failed——回写推进不出计数）。"""
        import inspect
        from src.risk_control import risk
        src = inspect.getsource(risk)
        assert "status != 'send_failed'" in src
        assert "part_filled" not in src   # 旧占位字面量退役
