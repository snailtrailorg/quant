"""安全态势查询（批 120 线②）：security_snapshot / security_finding 只读面。

本模块是安全域 SQL 唯一落点（test_no_sql_in_new_routes.py 立法：新路由文件禁
get_conn/内联 SQL）；业务判定（停留天数/基线 diff/组件变更）在路由层
web_api/routes/security.py——其中基线 diff 复用 health_monitor._baseline_diff
（判定单源：AI 只做摘要员，判定必须是确定性规则的同判件裁定）。
"""
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger("security_store")

_SNAP_COLS = ("scan_date, status, components, ports, ssh_cfg, dnf, headers, "
              "ai_summary, created_at")


def _rows_to_dicts(cur, cols: tuple[str, ...]) -> list[dict]:
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_snapshot(scan_date: date) -> dict | None:
    """当日快照行（六分区 jsonb 已反序列化为 dict）；无行返回 None（路由层转 404）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT {_SNAP_COLS} FROM security_snapshot WHERE scan_date = %s",
            (scan_date,))
        row = cur.fetchone()
    cols = ("scan_date", "status", "components", "ports", "ssh_cfg",
            "dnf", "headers", "ai_summary", "created_at")
    return dict(zip(cols, row)) if row else None


def get_latest_snapshot() -> dict | None:
    """最近一行快照（批 153：默认查询回落——每日 00:00-06:00 今日无行时空窗消除）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT {_SNAP_COLS} FROM security_snapshot ORDER BY scan_date DESC LIMIT 1")
        row = cur.fetchone()
    cols = ("scan_date", "status", "components", "ports", "ssh_cfg",
            "dnf", "headers", "ai_summary", "created_at")
    return dict(zip(cols, row)) if row else None


def get_prev_snapshot(scan_date: date) -> dict | None:
    """早于 scan_date 的最近一日快照（基线 diff 基线侧；同 run_security_scan 取法）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT components, ports, ssh_cfg FROM security_snapshot "
            "WHERE scan_date < %s ORDER BY scan_date DESC LIMIT 1", (scan_date,))
        row = cur.fetchone()
    cols = ("components", "ports", "ssh_cfg")
    return dict(zip(cols, row)) if row else None


def get_findings_active(day: date) -> list[dict]:
    """截至 day 的 finding 行（first_seen ≤ day——含已消失项：last_seen < day 由
    路由层标 fixed 灰显沉底，当日还在的 last_seen ≥ day 正常显示）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT source, pkg, vuln_id, fix_version, severity, first_seen, last_seen, "
            "last_seen_at, description, description_en "
            "FROM security_finding WHERE first_seen <= %s",
            (day,))
        cols = ("source", "pkg", "vuln_id", "fix_version", "severity",
                "first_seen", "last_seen", "last_seen_at", "description", "description_en")
        return _rows_to_dicts(cur, cols)


def get_component_ledger() -> list[dict]:
    """组件台账全行（批 123——D 区「上次变更日期」直读，替代 30 天窗口反查）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT source, name, version, origin, last_changed FROM security_component")
        cols = ("source", "name", "version", "origin", "last_changed")
        return _rows_to_dicts(cur, cols)


def get_snapshots_recent(days: int, today: date) -> list[dict]:
    """近 N 天快照行（升序；history 逐日循环的原料——含 ports/ssh_cfg 供基线 diff）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT {_SNAP_COLS} FROM security_snapshot "
            "WHERE scan_date > %s - %s::int ORDER BY scan_date", (today, days))
    cols = ("scan_date", "status", "components", "ports", "ssh_cfg",
            "dnf", "headers", "ai_summary", "created_at")
    return _rows_to_dicts(cur, cols)


def get_finding_spans() -> list[tuple]:
    """全表 (first_seen, last_seen) 二列——history 逐日活跃计数用（表小：漏洞数十行级）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute("SELECT first_seen, last_seen FROM security_finding")
        return cur.fetchall()


def delete_snapshot(scan_date: date) -> bool:
    """删一整日安全快照（批 140：AI 历史摘要弹窗的「删除条目」）。

    影响面=当日 findings/components 一并消失（快照是行粒度）；基线 diff 的
    「前一日」指针自动跳到更早一行（get_prev_snapshot 语义），无需修补。
    """
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM security_snapshots WHERE scan_date=%s", (scan_date,))
        conn.commit()
        return (cur.rowcount or 0) > 0


def delete_fixed_finding(pkg: str, vuln_id: str, today: date) -> str:
    """删一行**已修复** finding（批 126：显式删除取代隐藏 GC——用户裁定）。

    返回三态："ok"（已删）/ "not_found" / "active"（last_seen ≥ today=当前风险
    证据，误删=假绿——路由转 409）。判定与删除同事务窗口（无 TOCTOU）。
    """
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT last_seen FROM security_finding "
            "WHERE source='grype' AND pkg=%s AND vuln_id=%s", (pkg, vuln_id))
        row = cur.fetchone()
        if row is None:
            conn.commit()
            return "not_found"
        if row[0] >= today:
            conn.commit()
            return "active"
        conn.execute(
            "DELETE FROM security_finding WHERE source='grype' AND pkg=%s AND vuln_id=%s",
            (pkg, vuln_id))
        conn.commit()
    return "ok"
