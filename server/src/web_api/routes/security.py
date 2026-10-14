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

from fastapi import APIRouter, Depends, Query

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


def _enrich_findings(day: date, vulns_ok: bool = True,
                     ledger: dict[str, date] | None = None) -> list[dict]:
    """finding 行 → 前端形状：严重度/暴露时长/停留天数 + fixed 标记。

    fixed=last_seen < day 且当日 vulns 分区 ok（步 4 P1-2：降级日采集侧不推 last_seen
    ——_upsert_findings 仅 ok 分支，旧值保留 ⇒ last_seen<day 不构成「已修复」证据；
    降级日一律不判 fixed——宁多显示不可假清零）。
    expose_days（批 123）：台账 last_changed（漏洞版本的上线日）→ day——漏洞真实
    存在了多久；台账缺行（存量/未采）⇒ 回退 first_seen（发现日——宁保守）。
    """
    ledger = ledger or {}
    out = []
    for f in store.get_findings_active(day):
        first, last = f["first_seen"], f["last_seen"]
        base = ledger.get(f["pkg"]) or first          # 暴露起点：版本变更日；缺行回退发现日
        out.append({
            "source": f["source"], "pkg": f["pkg"], "vuln_id": f["vuln_id"],
            "fix_version": f["fix_version"], "severity": f["severity"],
            "first_seen": first.isoformat(), "last_seen": last.isoformat(),
            "dwell_days": max(_days_between(day, first), 0),
            "expose_days": max(_days_between(day, base), 0),
            "expose_est": f["pkg"] not in ledger,   # ≈ 标记：台账缺行=回退发现日（估计值）
            "fixed": vulns_ok and last < day,
        })
    # 排序（批 123·A07 §六）：已修复沉底 → 严重度降序（Critical 最高）→
    # 暴露时长降序 → pkg/vuln_id 稳定序。severity NULL 按 Unknown 档（最低）
    _sev_order = ("critical", "high", "medium", "low", "negligible", "unknown")

    def _sev_key(f):
        s = (f.get("severity") or "unknown").lower()
        return _sev_order.index(s) if s in _sev_order else len(_sev_order)

    out.sort(key=lambda x: (x["fixed"], _sev_key(x), -x["expose_days"],
                            -x["dwell_days"], x["pkg"], x["vuln_id"]))
    return out


def _count_new_findings(day: date, spans: list[tuple]) -> int:
    return sum(1 for first, _ in spans if first == day)


@router.get("/snapshot")
def security_snapshot(date_str: str = Query("", alias="date"),
                      _u: dict = Depends(require_perm("read"))):
    """当日安全快照（默认今日）：六分区 + findings + 与前一快照的基线 diff。

    参数名用 date_str + alias="date"（步 4 P0-2）：原名 `date` 遮蔽模块级
    `datetime.date`，无参默认路径 `date.today()` 炸 500——前端唯一入口即无参。
    alias 保持 `?date=` 对外契约不变。
    """
    day = _parse_date(date_str) if date_str else date.today()
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
    # vulns 分区不单独落列（快照 status 含 vulns 取最差）——整体 degraded ⇒ 保守
    # 视作 vulns 未证实，不判任何 fixed（P1-2：宁多显示不假清零）
    # 批 123：降级分区明细（A 区状态卡直读——六分区 status/degraded_reason 既有数据，
    # 此前前端从未渲染）；台账 pip 域→findings 暴露时长 + 直出 components_ledger
    ledger_rows = store.get_component_ledger()
    ledger = {r["name"]: r["last_changed"] for r in ledger_rows if r["source"] == "pip"}
    degraded_zones = [
        {"zone": z, "reason": (snap.get(z) or {}).get("degraded_reason") or ""}
        for z in ("headers", "ports", "ssh_cfg", "dnf", "components")
        if (snap.get(z) or {}).get("status") == "degraded"
    ]
    if snap["status"] == "degraded" and not degraded_zones:
        degraded_zones.append({"zone": "vulns", "reason": "grype 分区 degraded（快照 status 含最差）"})
    return {
        "status": snap["status"],
        "scan_date": day.isoformat(),
        "ai_summary": snap["ai_summary"] or "",
        "created_at": snap["created_at"].isoformat() if snap["created_at"] else None,
        "findings": _enrich_findings(day, vulns_ok=snap["status"] == "ok", ledger=ledger),
        "components": snap["components"],
        "components_ledger": [
            {"source": r["source"], "name": r["name"],
             "last_changed": r["last_changed"].isoformat()}
            for r in ledger_rows],
        "ports": snap["ports"],
        "ssh_cfg": snap["ssh_cfg"],
        "dnf": snap["dnf"],
        "headers": snap["headers"],
        "degraded_zones": degraded_zones,
        "baseline_diff": baseline_diff,
    }


@router.get("/history")
def security_history(days: int = 30, _u: dict = Depends(require_perm("read"))):
    """近 N 天逐日趋势：漏洞计数/新增/基线变更/组件变更（版本差真算）+ ai_summary 首行
    （B 区历史摘要折叠——日期+首行预览）。days 护栏 [1,31]（步 4 P1-5：>31 取 31——
    逐日行×基线 diff 的计算量与 payload 有界）。"""
    days = max(1, min(days, 31))
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
