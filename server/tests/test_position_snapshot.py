"""ST2 持仓真相源测试（N 审 v2 语义）：端点形状/幽灵缓存/挂点契约。

批 4a：写批单测（TestFlushPositions/TestAccountBaseline/真连接冒烟）收编 test_trading.py；
本文件留端点/缓存语义 + 源序挂点契约（守卫断言改指 trading.py——快照+持仓批已单源化）。
"""
from unittest.mock import patch, MagicMock
from types import SimpleNamespace


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
            MagicMock(fetchone=lambda: (1000000,)),  # 账户基线（#10：首条快照净值）
            MagicMock(fetchone=lambda: None),       # refresh 无行
        ]
        import src.web_api.routes.trading as trading_route
        with self._auth(), patch.object(trading_route, "get_conn", return_value=conn):
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
            MagicMock(fetchone=lambda: (1000000,)),   # 账户基线（#10）
            MagicMock(fetchone=lambda: (fresh_ts, 0)),   # refresh 新鲜且 rows=0
            MagicMock(fetchall=lambda: []),               # 快照空
        ]
        import src.web_api.routes.trading as trading_route
        with self._auth(), patch.object(trading_route, "get_conn", return_value=conn):
            r = self._client().get("/api/position", headers={"Authorization": "Bearer t"})
        body = r.json()
        assert body["stale"] is False and body["positions"] == []   # 新鲜空=真空仓
        assert body["snapshot_rows"] == 0

    def test_total_pnl_uses_account_baseline_not_config(self):
        """#10 口径修正（2026-08-22）：initial=账户首条快照净值，非 initial_capital 列。

        测试账户场景：total_value=10 亿、列值=策略配置 100 万 -> 原口径 total_pnl 虚增
        9.99 亿；改后以基线 10 亿起算 -> pnl=0。
        """
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.side_effect = [
            None,
            MagicMock(fetchone=lambda: (1_000_000_000, 0, 1_000_000)),   # 最新快照
            MagicMock(fetchone=lambda: (1_000_000_000,)),                 # 首条快照=基线
            MagicMock(fetchone=lambda: None),                             # refresh 无行
        ]
        import src.web_api.routes.trading as trading_route
        with self._auth(), patch.object(trading_route, "get_conn", return_value=conn):
            r = self._client().get("/api/position", headers={"Authorization": "Bearer t"})
        body = r.json()
        assert body["total_value"] == 1_000_000_000
        assert body["total_pnl"] == 0
        assert body["total_pnl_pct"] == 0


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
    """O 必查 7：挂点断线守卫/符号归一——源序断言锁语义（回退/挪位即红）。
    批 4a：快照+持仓批单源化 trading.snapshot_cycle，守卫断言改指 trading.py。"""

    def test_hub_flush_inside_account_guard(self):
        """O-F3：flush 必须在 query_account 守卫内（断线不写假空仓）——守卫现居 trading.snapshot_cycle。"""
        src = open("src/strategy_runner/trading.py").read()
        i_accounts = src.index("if not accounts:")
        i_flush = src.index("_flush_positions(adapter, account_id, tid)")
        assert i_flush > i_accounts, "snapshot_cycle 的 flush 必须在 query_account 守卫之后（断线双跳过）"

    def test_direct_flush_inside_else_branch(self):
        """快照写库（含 available_cash 列）与 flush 同分支：flush 在 INSERT 之后。"""
        src = open("src/strategy_runner/trading.py").read()
        i_insert = src.index("INSERT INTO account_snapshot (total_value, daily_pnl, initial_capital, available_cash)")   # DB 优化批 2026-08-21 加列
        i_flush = src.index("_flush_positions(adapter, account_id, tid)")
        assert i_flush > i_insert, "flush 应在快照写库之后（同 else 分支）"

    def test_reconcile_normalizes_symbols_both_sides(self):
        """O-F2：reconcile 两侧符号必须 split_part 归一（vt_symbol vs 裸 ticker 命名空间）。"""
        src = open("src/scheduler/tasks.py").read()
        i_rec = src.index("持仓账实分离")
        seg = src[i_rec - 1500:i_rec]
        assert seg.count("split_part(symbol, '.', 1)") >= 2, "join 两侧都要归一"


class TestRealConnectionSmoke:
    """O 必查 7③：真 psycopg 连接冒烟——批 4a 随 _flush_positions 收编 test_trading.py。"""
