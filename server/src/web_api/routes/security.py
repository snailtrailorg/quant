"""批 120 线②：安全态势只读端点（快照 + 历史）。SQL 全在 data_platform/security_store
（路由层 SQL 冻结立法——新路由文件禁 DB 连接直取，test_no_sql_in_new_routes 守门）。

- GET /api/security/snapshot?date=YYYYMMDD：当日快照 + findings（停留天数降序，
  已修复灰显所需 fixed 标记）+ 与前一日快照的基线 diff（判定复用
  health_monitor._baseline_diff——采集告警与展示同一套确定性规则，不造第二真源）。
- GET /api/security/history?days=30：逐日漏洞计数/新增/基线变更/组件变更
  （components jsonb 逐键版本差真算——D 区「距上次变更天数」依赖）。
- 权限：require_perm("read")（同判件裁定复用免连锁）。
"""
from __future__ import annotations

import logging
import re
from datetime import date

from fastapi import APIRouter, Depends

from src.data_platform import security_store as store
from src.web_api.auth import require_perm
from src.web_api.errors import ApiError

logger = logging.getLogger("web_api")

router = APIRouter(prefix="/api/security", tags=["security"])

_DATE_RE = re.compile(r"^\d{8}$")

# D 区组件备注（静态映射：版本号看不出维护状态——al8 backport 语义须人写）
COMPONENT_NOTES = {
    "openssl": "al8 backport 维护（rpm 版本≠上游版本，以 dnf --security 为准）",
}


def _parse_date(raw: str) -> date:
    if not _DATE_RE.fullmatch(raw or ""):
        raise ApiError(400, "SECURITY_DATE_INVALID", f"date 须为 YYYYMMDD，实得: {raw!r}")
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError as e:
        raise ApiError(400, "SECURITY_DATE_INVALID", f"非法日期: {raw}（{e}）") from e


def _days_between(later: date, earlier: date) -> int:
    return (later - earlier).days


def _enrich_findings(day: date) -> list[dict]:
    """finding 行 → 前端形状：停留天数（day - first_seen）+ fixed 标记（last_seen < day
    ⇒ 采集降级期旧值，或快照日复扫口径——灰显沉底用）。"""
    out = []
    for f in store.get_findings_active(day):
        first, last = f["first_seen"], f["last_seen"]
        out.append({
            "source": f["source"], "pkg": f["pkg"], "vuln_id": f["vuln_id"],
            "fix_version": f["fix_version"],
            "first_seen": first.isoformat(), "last_seen": last.isoformat(),
            "dwell_days": max(_days_between(day, first), 0),
            "fixed": last < day,
        })
    # 停留天数降序（同天数按 pkg 稳定序）；已修复（fixed）沉底
    out.sort(key=lambda x: (x["fixed"], -x["dwell_days"], x["pkg"], x["vuln_id"]))
    return out


def _count_new_findings(day: date, spans: list[tuple]) -> int:
    return sum(1 for first, _ in spans if first == day)


@router.get("/snapshot")
def security_snapshot(date: str = "", _u: dict = Depends(require_perm("read"))):
    """当日安全快照（默认今日）：六分区 + findings + 与前一快照的基线 diff。"""
    day = _parse_date(date) if date else date.today()
    snap = store.get_snapshot(day)
    if snap is None:
        raise ApiError(404, "SECURITY_SNAPSHOT_NOT_FOUND",
                       f"{day.isoformat()} 无安全快照（beat 每日 06:00 采集，无行=当日未扫）")

    prev = store.get_prev_snapshot(day)
    diff_items = _baseline_diff_safe(prev, {
        "components": snap["components"], "ports": snap["ports"],
        "ssh_cfg": snap["ssh_cfg"],
    })
    baseline_diff = {
        "new_ports": [d["detail"] for d in diff_items if d["type"] == "port"],
        "new_packages": [d["detail"] for d in diff_items if d["type"] == "package"],
        "cfg_flips": [d["detail"] for d in diff_items if d["type"] == "config"],
    }
    return {
        "status": snap["status"],
        "scan_date": day.isoformat(),
        "ai_summary": snap["ai_summary"] or "",
        "created_at": snap["created_at"].isoformat() if snap["created_at"] else None,
        "findings": _enrich_findings(day),
        "components": snap["components"],
        "ports": snap["ports"],
        "ssh_cfg": snap["ssh_cfg"],
        "dnf": snap["dnf"],
        "headers": snap["headers"],
        "baseline_diff": baseline_diff,
    }


@router.get("/history")
def security_history(days: int = 30, _u: dict = Depends(require_perm("read"))):
    """近 N 天逐日趋势：漏洞计数/新增/基线变更/组件变更（版本差真算）+ ai_summary 首行
    （B 区历史摘要折叠——日期+首行预览）。"""
    days = max(1, min(days, 120))
    today = date.today()
    snaps = store.get_snapshots_recent(days, today)
    spans = store.get_finding_spans()

    rows: list[dict] = []
    prev_snap: dict | None = None
    for s in snaps:
        d = s["scan_date"]
        vuln_count = sum(1 for first, last in spans if first <= d <= last)
        cur_triple = {"components": s["components"], "ports": s["ports"],
                      "ssh_cfg": s["ssh_cfg"]}
        prev_triple = ({"components": prev_snap["components"],
                        "ports": prev_snap["ports"], "ssh_cfg": prev_snap["ssh_cfg"]}
                       if prev_snap else None)
        diff_items = _baseline_diff_safe(prev_triple, cur_triple)
        summary = s["ai_summary"] or ""
        rows.append({
            "scan_date": d.isoformat(),
            "status": s["status"],
            "vuln_count": vuln_count,
            "new_vulns": _count_new_findings(d, spans),
            "baseline_changes": len(diff_items),
            "component_changes": _component_changes(prev_triple, cur_triple),
            "ai_summary_preview": summary.splitlines()[0][:120] if summary else "",
        })
        prev_snap = s
    rows.reverse()   # 近日在前
    return {"days": days, "items": rows}


def _baseline_diff_safe(prev: dict | None, cur: dict) -> list[dict]:
    """采集器同款判定的展示侧调用（fail-open：判定器异常不炸端点——只读面不因展示
    连坐 500）。"""
    try:
        from src.health_monitor.security_collector import _baseline_diff
        return _baseline_diff(prev, cur)
    except Exception as e:
        logger.warning("基线 diff 展示侧失败（返回空）: %s", e)
        return []


def _component_changes(prev: dict | None, cur: dict) -> list[str]:
    """前后快照 components jsonb 逐键版本差（首日 prev=None ⇒ 空——与基线 diff 同口径）。

    对比面=components 分区内**所有标量键**（pip 子 dict 逐包 + nginx/openssl/pg/redis
    顶层版本串）——「版本变动」取值不同即计入（新增键=新组件安装也计入；值 None→非 None
    同理）。返回变动的组件名数组（D 区「距上次变更天数」由此反查）。
    """
    if not prev:
        return []
    out: set[str] = set()

    def _walk(p: dict | None, c: dict | None):
        p, c = p or {}, c or {}
        for k in set(p) | set(c):
            pv, cv = p.get(k), c.get(k)
            if isinstance(pv, dict) or isinstance(cv, dict):
                _walk(pv if isinstance(pv, dict) else None,
                      cv if isinstance(cv, dict) else None)
            elif pv != cv:
                out.add(k)

    _walk(prev.get("components"), cur.get("components"))
    return sorted(out)
