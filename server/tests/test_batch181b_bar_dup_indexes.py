"""批181b 守门：bar_* 表不得有同键双索引（唯一约束 + 普通索引）。

病根：0001/0022/0064/0065 四迁移统一模板重复建索引；修复=0186 在线删除，
init-schema.sql 同步去掉。真库守门钉「迁移后每表在 (symbol,ts) 最多一个索引」。
"""
from pathlib import Path

_MIG_TABLES = ["bar_1d", "bar_1min", "bar_5min", "bar_15min", "bar_30min",
               "bar_60min", "bar_1h", "bar_4h", "bar_index"]


def test_migration_covers_all_bar_tables():
    p = Path(__file__).resolve().parents[1] / "migrations/versions/0186_b181b_bar_dup_indexes.py"
    src = p.read_text(encoding="utf-8")
    for t in _MIG_TABLES:
        assert f'"{t}"' in src, t


def test_init_schema_has_no_dup_index():
    p = Path(__file__).resolve().parents[1] / "scripts/init-schema.sql"
    assert "idx_bar_1d_symbol_ts" not in p.read_text(encoding="utf-8")


def test_no_duplicate_symbol_ts_indexes_in_db():
    """真库：每张 bar 表在 (symbol, ts) 上最多一个索引（迁移 0186 后）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tablename, count(*) AS n FROM pg_indexes "
            "WHERE schemaname='public' AND tablename LIKE 'bar\\_%' "
            "AND indexdef LIKE '%(symbol, ts%' "
            "GROUP BY tablename HAVING count(*) > 1").fetchall()
    assert rows == [], f"重复 (symbol,ts) 索引: {rows}"
