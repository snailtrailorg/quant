"""批184a 守门：组件台账域归一（source=域 pip/os/binary，实现=pkg_mgr）。

守门原则（批171 纪律）：断言**行为与形态**，不钉实现细节字面量；真库项在无库时跳过。
"""
import datetime as _dt
import inspect
import re

import pytest


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM security_component LIMIT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


@needs_db
def test_domain_values_closed():
    """域值域只剩 pip/os/binary —— rpm/system 不得残留（CHECK 约束同口径）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT source FROM security_component").fetchall()
    assert {r[0] for r in rows} <= {"pip", "os", "binary"}, rows


@needs_db
def test_domain_check_constraint_present():
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid='security_component'::regclass "
            "AND conname IN ('ck_sc_domain','ck_sc_pkg_mgr')").fetchall()
    assert {r[0] for r in rows} == {"ck_sc_domain", "ck_sc_pkg_mgr"}


@needs_db
def test_same_name_two_domains_coexist():
    """同名不同域是两个实体：pip tzdata 与 os tzdata 各一行，managed_by 各自正确。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT source, managed_by, judge_class FROM security_component "
            "WHERE lower(name)='tzdata'").fetchall()
    m = {r[0]: (r[1], r[2]) for r in rows}
    if "pip" not in m or "os" not in m:
        pytest.skip("dev 库无 tzdata 双域样本")
    assert m["pip"][0] == "pypi"
    assert m["os"][0] == "repo"


@needs_db
def test_alias_rows_removed():
    """8 个点名探针别名不是包名，已删（真实名 python3.11 等由全量 OS 行覆盖）。"""
    from src.data_platform.db import get_conn
    alias = ("python", "nginx", "openssl", "postgres", "redis",
             "pango", "cairo", "wqy-microhei")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM security_component WHERE source='os' AND name = ANY(%s)",
            (list(alias),)).fetchall()
    assert rows == [], rows


@needs_db
def test_upsert_separates_domain_from_impl():
    """域与实现解耦：同一域 os 可携带 pkg_mgr=apt，managed_by 仍由域派生（repo）。"""
    from src.data_platform.db import get_conn
    from src.health_monitor import security_collector as sc
    probe = "__b184a_probe__"
    with get_conn() as conn:
        conn.execute("DELETE FROM security_component WHERE name=%s", (probe,))
        sc._upsert_components(conn, _dt.date.today(),
                              [("os", probe, "1.0", "OS apt")], pkg_mgr="apt")
        row = conn.execute(
            "SELECT source, pkg_mgr, managed_by FROM security_component WHERE name=%s",
            (probe,)).fetchone()
        conn.execute("DELETE FROM security_component WHERE name=%s", (probe,))
        conn.commit()
    assert row == ("os", "apt", "repo"), row


def test_gc_not_scoped_by_source():
    """孤儿 GC 必须**全域**（原仅 system/pip ⇒ rpm·binary 永久累积）。"""
    from src.health_monitor import security_collector as sc
    src_text = inspect.getsource(sc.run_security_scan)
    m = re.search(r"DELETE FROM security_component WHERE last_seen < %s", src_text)
    assert m, "统一 GC 语句缺失"
    assert "source=" not in src_text[m.start():m.end() + 80], "GC 不得按 source 限定"


def test_audit_writes_domain_not_impl_source():
    """dep_audit 的 OS 行写域名 'os'（实现走 pkg_mgr），不得回退 'system'。"""
    from src.scheduler import tasks
    s = inspect.getsource(tasks.dep_audit)
    # 只取代码行——注释里提到 'system'（说明历史）不算回退
    body = "\n".join(ln for ln in s.splitlines() if not ln.strip().startswith("#"))
    assert "VALUES ('os', %s, %s, %s" in body
    assert "'system'" not in body
