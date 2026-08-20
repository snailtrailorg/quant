"""ST2 持仓真相源测试（N 审 v2 语义）：覆盖式写批/空批可表示/stale 区分/幽灵缓存清除/端点形状。"""
from unittest.mock import patch, MagicMock
from types import SimpleNamespace


def _pos(symbol="600000.SHSE", volume=1000, direction="long", frozen=200, yd=800):
    return SimpleNamespace(symbol=symbol, volume=volume, avg_price=9.05, pnl=50.0,
                           direction=direction, frozen=frozen, yd_volume=yd)


class TestFlushPositions:
    def _run(self, positions, account_id="253191001822", task_id=8):
        from src.strategy_runner import main as runner
        import src.data_platform.db as db
        adapter = MagicMock()
        adapter.query_position.return_value = positions
        conn = MagicMock()
        conn.__enter__.return_value = conn
        with patch.object(db, "get_conn", return_value=conn):
            runner._flush_positions(adapter, account_id, task_id)
        return conn

    def test_overwrite_write_single_transaction(self):
        """N-v2：单事务 DELETE 该账户 + INSERT 批 + upsert refresh（一次 commit）。"""
        conn = self._run([_pos()])
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        assert any("DELETE FROM position_snapshot" in s for s in sqls)
        assert any("ON CONFLICT (account_id)" in s for s in sqls)   # refresh upsert
        # O-F1：executemany 走 cursor（池化连接无此方法——F 审同款坑的回归锁）
        cur = conn.cursor.return_value.__enter__.return_value
        cur.executemany.assert_called_once()
        assert "ON CONFLICT (account_id, symbol, direction)" in cur.executemany.call_args.args[0]
        conn.commit.assert_called_once()   # 单事务

    def test_empty_batch_clears_state_and_writes_heartbeat(self):
        """N-F1：清仓 0 行——DELETE 仍执行（表空=空仓），refresh rows=0（空批可表示）。"""
        conn = self._run([])
        conn.cursor.assert_not_called()   # 空批不 INSERT
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        assert any("DELETE FROM position_snapshot" in s for s in sqls)
        upsert = [c for c in conn.execute.call_args_list if "ON CONFLICT (account_id)" in c.args[0]]
        assert upsert and upsert[0].args[1] == ("253191001822", 0, "8", 0, "8")

    def test_account_id_none_becomes_default(self):
        conn = self._run([], account_id=None)
        sqls = [c.args[0] for c in conn.execute.call_args_list]
        del_call = [c for c in conn.execute.call_args_list if "DELETE" in c.args[0]][0]
        assert del_call.args[1] == ("default",)

    def test_failure_does_not_raise(self):
        """写批失败仅日志（不阻断主循环）。"""
        from src.strategy_runner import main as runner
        adapter = MagicMock()
        adapter.query_position.side_effect = Exception("TD 断线")
        import src.data_platform.db as db
        with patch.object(db, "get_conn", side_effect=Exception("PG down")):
            runner._flush_positions(adapter, "x", 1)   # 不抛即过

    def test_short_rows_written_not_filtered(self):
        """N-S3：两融 Short 行如实写（不过滤），端点侧再选向。"""
        conn = self._run([_pos(), _pos(symbol="600000.SHSE", volume=100, direction="short")])
        cur = conn.cursor.return_value.__enter__.return_value
        rows = cur.executemany.call_args.args[1]
        assert len(rows) == 2 and rows[1][2] == "short"


class TestPositionEndpoint:
    def _client(self):
        from fastapi.testclient import TestClient
        from src.web_api.main import app
        return TestClient(app)

    def _auth(self):
        from unittest.mock import patch as _p
        from src.web_api import auth as _auth_mod
        return _p.object(_auth_mod, "verify_jwt",
                         return_value={"sub": "1", "username": "admin", "role": "admin"})

    def test_stale_when_never_refreshed(self):
        import datetime as dt
        conn = MagicMock()
        conn.__enter__.return_value = conn
        # account_snapshot 有行；position_refresh 查询返回 None（从未跑过）
        conn.execute.side_effect = [
            None,                                   # SELECT 1 account_snapshot
            MagicMock(fetchone=lambda: (1000000, 0, 1000000)),  # 总资产
            MagicMock(fetchone=lambda: None),       # refresh 无行
        ]
        import src.web_api.main as web_main
        with self._auth(), patch.object(web_main, "get_conn", return_value=conn):
            r = self._client().get("/api/position", headers={"Authorization": "Bearer t"})
        body = r.json()
        assert body["stale"] is True and body["positions"] == []   # 从未跑≠空仓
        assert "snapshot_ts" in body and "snapshot_rows" in body

    def test_fresh_empty_is_flat_not_stale(self):
        import datetime as dt
        fresh_ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.side_effect = [
            None,
            MagicMock(fetchone=lambda: (1000000, 0, 1000000)),
            MagicMock(fetchone=lambda: (fresh_ts, 0)),   # refresh 新鲜且 rows=0
            MagicMock(fetchall=lambda: []),               # 快照空
        ]
        import src.web_api.main as web_main
        with self._auth(), patch.object(web_main, "get_conn", return_value=conn):
            r = self._client().get("/api/position", headers={"Authorization": "Bearer t"})
        body = r.json()
        assert body["stale"] is False and body["positions"] == []   # 新鲜空=真空仓
        assert body["snapshot_rows"] == 0


class TestAdapterGhostCache:
    def test_query_position_clears_cache_first(self):
        """N-S6：查询前清缓存——清仓标的不再从只增缓存返回幽灵仓。"""
        from src.strategy_framework.adapters import XTPAdapter
        adapter = XTPAdapter.__new__(XTPAdapter)   # 跳过 __init__
        adapter._gateway = MagicMock()
        adapter._lock = __import__("threading").Lock()
        adapter._positions = {"ghost.SHSE": SimpleNamespace(vt_symbol="ghost.SHSE", volume=999,
                                                            price=1.0, pnl=0.0)}
        adapter._gateway.query_position.return_value = None
        # _wait_update 打桩为立即返回（无新事件→缓存保持清空）
        with patch.object(XTPAdapter, "_wait_update"):
            result = adapter.query_position()
        assert result == [], "幽灵缓存必须被清除，清仓后返回空"


class TestOWiringContracts:
    """O 必查 7：挂点断线守卫/符号归一——源序断言锁语义（回退/挪位即红）。"""

    def test_hub_flush_inside_account_guard(self):
        """O-F3：hub 的 flush 必须在 query_account 守卫内（断线不写假空仓）。"""
        src = open("src/strategy_runner/hub_worker.py").read()
        i_accounts = src.index("if accounts:")
        i_flush = src.index("_flush_positions(adapter")
        assert i_flush > i_accounts, "hub flush 必须在 if accounts: 守卫之后（断线双跳过）"

    def test_direct_flush_inside_else_branch(self):
        """direct 的 flush 在快照 else 分支内（TD 断线跳过快照时不刷持仓）。"""
        src = open("src/strategy_runner/main.py").read()
        i_insert = src.index("INSERT INTO account_snapshot (total_value, daily_pnl, initial_capital, available_cash)")   # DB 优化批 2026-08-21 加列
        i_flush = src.index("_flush_positions(adapter, account_id, tid)")
        assert i_flush > i_insert, "direct flush 应在快照写库之后（同 else 分支）"

    def test_reconcile_normalizes_symbols_both_sides(self):
        """O-F2：reconcile 两侧符号必须 split_part 归一（vt_symbol vs 裸 ticker 命名空间）。"""
        src = open("src/scheduler/tasks.py").read()
        i_rec = src.index("持仓账实分离")
        seg = src[i_rec - 1500:i_rec]
        assert seg.count("split_part(symbol, '.', 1)") >= 2, "join 两侧都要归一"


class TestRealConnectionSmoke:
    """O 必查 7③：真 psycopg 连接冒烟（本地 dev DB；不可达则跳过）。"""

    def test_flush_roundtrip_on_real_db(self):
        import pytest
        try:
            import src.data_platform.db as db
            with db.get_conn() as conn:
                conn.execute("SELECT 1 FROM position_snapshot LIMIT 1")
                conn.commit()
        except Exception:
            pytest.skip("本地 PG 不可达或 0043 未迁移")

        from src.strategy_runner import main as runner
        adapter = MagicMock()
        adapter.query_position.return_value = [_pos()]
        runner._flush_positions(adapter, "smoke_test_acct", 99)
        import src.data_platform.db as db
        try:
            with db.get_conn() as conn:
                cur = conn.execute("SELECT symbol, volume, direction FROM position_snapshot "
                                   "WHERE account_id=%s", ("smoke_test_acct",))
                rows = cur.fetchall()
                cur = conn.execute("SELECT rows, task_id FROM position_refresh "
                                   "WHERE account_id=%s", ("smoke_test_acct",))
                ref = cur.fetchone()
            assert rows and rows[0][0] == "600000.SHSE" and rows[0][2] == "long"
            assert ref and ref[0] == 1 and ref[1] == "99"
            # 空批覆盖（N-F1 全链）：再跑空批 → 行清空 refresh rows=0
            adapter2 = MagicMock()
            adapter2.query_position.return_value = []
            runner._flush_positions(adapter2, "smoke_test_acct", 99)
            with db.get_conn() as conn:
                cur = conn.execute("SELECT count(*) FROM position_snapshot WHERE account_id=%s",
                                   ("smoke_test_acct",))
                assert cur.fetchone()[0] == 0, "空批必须清掉旧行（当前状态表语义）"
                cur = conn.execute("SELECT rows FROM position_refresh WHERE account_id=%s",
                                   ("smoke_test_acct",))
                assert cur.fetchone()[0] == 0
        finally:
            with db.get_conn() as conn:
                conn.execute("DELETE FROM position_snapshot WHERE account_id=%s", ("smoke_test_acct",))
                conn.execute("DELETE FROM position_refresh WHERE account_id=%s", ("smoke_test_acct",))
                conn.commit()
