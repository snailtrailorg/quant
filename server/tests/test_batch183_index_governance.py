"""批183 守门：索引只由迁移建（禁运行时 DDL）；UPSERT 值未变不更新；同键索引唯一。"""
import pathlib


def test_upsert_overwrite_is_conditional():
    from src.data_platform import schema
    sql = schema.BAR_TABLE_INSERT_OVERWRITE
    assert "IS DISTINCT FROM" in sql
    assert "WHERE (" in sql


def test_no_runtime_create_index_in_src():
    import re
    rx = re.compile(r"CREATE\s+(TABLE|INDEX)\s+(IF NOT EXISTS\s+)?[\w\"]", re.I)
    root = pathlib.Path(__file__).resolve().parents[1] / "src"
    bad = []
    for p in root.rglob("*.py"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if (s.startswith("#") or s.startswith('"""') or s.startswith("'''")
                    or "logger." in s):
                continue
            if rx.search(s):
                bad.append(f"{p.name}:{i}: {s[:80]}")
    assert not bad, "运行时 DDL 残留（索引/表只由迁移建）:\n" + "\n".join(bad)


def test_retention_sweep_notes_bulk_delete_without_alert():
    """大批删除只记日志（季度人工评审），不新增告警码/runbook 条目。"""
    import inspect
    from src.scheduler import tasks
    s = inspect.getsource(tasks.bar_retention_sweep)
    assert "100_000" in s
    assert "reindex_hint" not in s


def test_unused_indexes_dropped():
    """迁移 0188：三个零使用/重复索引已删。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
            "AND indexname IN ('idx_user_tokens_token','idx_margin_code',"
            "'idx_system_log_level')").fetchall()
    assert rows == [], rows


def test_daily_basic_has_single_ts_code_index():
    """真库：daily_basic 在 (ts_code, trade_date) 上至多一个索引（迁移 0187 后）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
            "AND tablename='daily_basic' AND indexdef LIKE '%(ts_code, trade_date%'"
        ).fetchall()
    assert len(rows) <= 1, rows
