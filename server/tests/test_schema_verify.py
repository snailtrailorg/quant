"""#48 schema 列级校验测试（L 审修正案语义）：纯函数单向存在性 + 生成式期望清单 + 入口路由。"""
from unittest.mock import patch, MagicMock


def _mock_conn(columns: dict):
    """columns: {table: set(cols)} 模拟 information_schema 查询结果。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    rows = [(t, c) for t, cols in columns.items() for c in sorted(cols)]
    conn.execute.return_value.fetchall.return_value = rows
    return conn


class TestVerifySchema:
    def test_clean_schema_no_findings(self):
        from src.data_platform import db
        exp = {"users": {"id", "username"}, "order_log": {"id", "ts"}}
        conn = _mock_conn({"users": {"id", "username"}, "order_log": {"id", "ts", "extra"}})
        with patch.object(db, "load_schema_expectations", return_value=exp), \
             patch.object(db, "get_conn", return_value=conn):
            r = db.verify_schema()
        assert r == {"missing_tables": [], "missing_columns": {}}

    def test_missing_column_detected(self):
        """0038 类问题：表在但缺列（单向 expected⊆actual）。"""
        from src.data_platform import db
        exp = {"strategy_config": {"id", "updated_at"}}
        conn = _mock_conn({"strategy_config": {"id", "name"}})   # 缺 updated_at
        with patch.object(db, "load_schema_expectations", return_value=exp), \
             patch.object(db, "get_conn", return_value=conn):
            r = db.verify_schema()
        assert r["missing_columns"] == {"strategy_config": ["updated_at"]}
        assert r["missing_tables"] == []

    def test_missing_table_detected(self):
        from src.data_platform import db
        exp = {"users": {"id"}, "ghost_table": {"x"}}
        conn = _mock_conn({"users": {"id", "more"}})
        with patch.object(db, "load_schema_expectations", return_value=exp), \
             patch.object(db, "get_conn", return_value=conn):
            r = db.verify_schema()
        assert r["missing_tables"] == ["ghost_table"]

    def test_single_query_no_per_table_probing(self):
        """L：一条 information_schema 查询拿全（非逐表 SELECT 1）。"""
        from src.data_platform import db
        conn = _mock_conn({"users": {"id"}})
        with patch.object(db, "load_schema_expectations", return_value={"users": {"id"}}), \
             patch.object(db, "get_conn", return_value=conn):
            db.verify_schema()
        assert conn.execute.call_count == 1
        sql = conn.execute.call_args.args[0]
        assert "information_schema.columns" in sql and "ANY(" in sql

    def test_expectations_file_is_generated_shape(self):
        """提交的期望清单=链生成物：非空、含收编后的 strategy_config（带 updated_at）、bar 小写。"""
        from src.data_platform.db import load_schema_expectations
        exp = load_schema_expectations()
        assert len(exp) >= 45, f"链产物应 ~50 表，实际 {len(exp)}"
        assert "strategy_config" in exp and "updated_at" in exp["strategy_config"]
        assert "bar_1d" in exp and "bar_1D" not in exp    # 0001 大小写修正后
        assert "health_event" in exp                       # 0041


class TestEntryRouting:
    def test_report_routes_findings(self):
        from src.health_monitor import monitor
        notified, events = [], []
        with patch.object(monitor, "_notify", side_effect=lambda *a, **k: notified.append(a)), \
             patch.object(monitor, "_write_event", side_effect=lambda *a, **k: events.append(a)):
            monitor.report_schema_findings(
                {"missing_tables": ["ghost"], "missing_columns": {"t": ["c"]}})
        assert notified and events
        assert events[0][0] == "schema_drift"
        # W3 盲审 A-P0 盲区防线：title/body 逗号被吞(隐式拼接)时 body 挤进 title——
        # 断言三位置参齐+body 非空,此类回归当场红
        assert len(notified[0]) == 3 and notified[0][2], notified[0]

    def test_report_noop_on_clean(self):
        from src.health_monitor import monitor
        with patch.object(monitor, "_notify") as n, patch.object(monitor, "_write_event") as w:
            monitor.report_schema_findings({"missing_tables": [], "missing_columns": {}})
        n.assert_not_called()
        w.assert_not_called()


class TestEnsureTableVerify:
    """ensure_table 改 verify（2026-09-03）：校验存在+告警，不再运行时建表。"""

    def test_caches_on_exist(self):
        from src.data_platform import db
        db._verified_tables.clear()
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = ("bar_1d",)  # to_regclass 命中（非 None）
        with patch.object(db, "get_conn", return_value=conn):
            db.ensure_table("1D")
            db.ensure_table("1d")  # 同表（lower 后 bar_1d），第二次应缓存不查
        assert conn.execute.call_count == 1   # 只查一次 to_regclass
        db._verified_tables.clear()

    def test_not_cached_on_missing(self):
        from src.data_platform import db
        db._verified_tables.clear()
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = (None,)  # to_regclass 未命中
        with patch.object(db, "get_conn", return_value=conn):
            db.ensure_table("60min")
            db.ensure_table("60min")  # 表缺失不缓存，第二次仍查（迁移跑完能捡到）
        assert conn.execute.call_count == 2
        db._verified_tables.clear()
