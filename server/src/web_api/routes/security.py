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
from src.web_api.auth import audit_log, require_perm
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
                     ledger: dict[str, date] | None = None,
                     snap_created_at=None) -> list[dict]:
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
            "description": f.get("description") or "",
            "description_en": f.get("description_en") or "",
            "first_seen": first.isoformat(), "last_seen": last.isoformat(),
            # 批 154：CVE 公开日（grype DB 真值；NULL=未知——前端回退 first_seen 灰显）
            "published": f["published_date"].isoformat() if f.get("published_date") else None,
            "dwell_days": max(_days_between(day, first), 0),
            "expose_days": max(_days_between(day, base), 0),
            "expose_est": f["pkg"] not in ledger,   # ≈ 标记：台账缺行=回退发现日（估计值）
            # fixed 判据（批 124 同日复跑重判）：本轮报了 ⇒ last_seen_at=clock_timestamp()
            # （晚于快照 created_at=now()=事务开始）⇒ 未 fixed；本轮未报 ⇒ 停在旧时刻
            # ⇒ fixed（同日升级当日沉底，不必等次日）。无 created_at（异常）=回退日粒度
            "fixed": vulns_ok and (
                f["last_seen_at"] < snap_created_at if (f.get("last_seen_at") and snap_created_at)
                else last < day),
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


@router.post("/scan")
def security_scan_trigger(payload: dict = Depends(require_perm("data_sync"))):
    """手动触发安全扫描（批 138；批 155 串行化 §4.4c）。

    Valkey sec:scan_lock（TTL>任务 time_limit）：占用中 -> 409 SEC_SCAN_RUNNING
    （拒绝入队而非排队）；任务收尾 DEL（值=自己）；崩溃 TTL 兜底。缓存
    stale 顺带 async 派 dep_audit（fresh 线=DEP_AUDIT_FRESH_H 单源）。
    """
    import json as _json
    import datetime as _dt
    from src.quant_common.redis_client import business_redis
    from src.scheduler.tasks import security_scan
    br = business_redis()
    lock_val = br.get("sec:scan_lock")
    if isinstance(lock_val, bytes):
        lock_val = lock_val.decode()
    if lock_val:
        raise ApiError(409, "SEC_SCAN_RUNNING", f"扫描进行中（task={lock_val[:8]}）")
    try:
        from src.quant_common.depjudge import DEP_AUDIT_FRESH_H
        raw = br.get("sec:dep_audit")
        fresh = False
        if raw:
            c = _json.loads(raw)
            # 批 155 §4.1 契约：schema 不识别=视为无缓存（旧 154 结构 ts 新鲜也不能
            # 压住新 audit 派发——prod 实证：昨晚旧缓存 age 12.7h 判 fresh 导致
            # 新 audit 三小时未被派发、采集侧全 unknown）
            if c.get("schema_version") == 1:
                ts = _dt.datetime.fromisoformat(c["ts"])
                fresh = (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds() < DEP_AUDIT_FRESH_H * 3600
        if not fresh:
            from src.scheduler.tasks import dep_audit
            dep_audit.delay()
            logger.info("dep_audit dispatched (cache stale/missing)")
    except ApiError:
        raise
    except Exception as _e:
        logger.warning("dep_audit dispatch probe failed: %s", _e)   # 批155 验证期：不再静默
    task = security_scan.delay()
    try:
        br.set("sec:scan_lock", str(task.id), ex=2700)
    except Exception:
        pass
    audit_log(payload.get("username", ""), "trigger_security_scan", "")
    logger.info("security scan triggered by %s (task=%s)", payload.get("username"), task.id)
    return {"status": "submitted", "task_id": task.id}


def _hu(judge_class):
    from src.quant_common.depjudge import has_update_of
    return has_update_of(judge_class)


def _stale_days(row):
    lc = row.get("last_changed")
    if not lc:
        return None
    return (date.today() - lc).days


def _dep_health_summary(snap, ledger_rows):
    """批 155：依赖健康汇总（主展示数=has_update_n；细类供 tooltip/折叠头）。"""
    from collections import Counter
    comp = snap.get("components") or {}
    dh = comp.get("dep_health") or {}
    cnt = Counter(r.get("judge_class") or "unknown" for r in ledger_rows)
    has_n = sum(1 for r in ledger_rows if _hu(r.get("judge_class")) == "yes")
    dev_n = sum(1 for r in (dh.get("rows") or []) if r.get("group") == "dev")
    return {"has_update_n": has_n,
            "actionable": cnt.get("actionable", 0),
            "in_constraint": cnt.get("in_constraint", 0),
            "pinned": cnt.get("pinned", 0), "drift": cnt.get("drift", 0),
            "unreachable": cnt.get("unreachable", 0), "unknown": cnt.get("unknown", 0),
            "dev_n": dev_n, "checked_at": dh.get("checked_at"),
            "fresh": dh.get("fresh"), "pkg_mgr": dh.get("pkg_mgr"),
            "errors": dh.get("errors") or []}


@router.get("/dep-audit-debug")
def dep_audit_debug(_u: dict = Depends(require_perm("data_sync"))):
    """批 155 验证期：读 sec:dep_audit 原始缓存（排查采集/派发链；key 只读）。"""
    import datetime as _dt
    import json as _json
    from src.quant_common.redis_client import business_redis
    from src.quant_common.depjudge import DEP_AUDIT_FRESH_H
    raw = business_redis().get("sec:dep_audit")
    if not raw:
        return {"exists": False, "fresh_h_line": DEP_AUDIT_FRESH_H}
    c = _json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    _ts_raw = str(c.get("ts") or "1970-01-01T00:00:00+00:00")
    if "+00:00-" in _ts_raw:                       # 容错：历史 run_id 后缀形态（切后补 tz 防 naive）
        _ts_raw = _ts_raw.split("+00:00-")[0] + "+00:00"
    ts = _dt.datetime.fromisoformat(_ts_raw)
    age_h = (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds() / 3600
    return {"exists": True, "schema_version": c.get("schema_version"),
            "ts": c.get("ts"), "age_h": round(age_h, 2),
            "fresh": age_h <= DEP_AUDIT_FRESH_H, "ok": c.get("ok"),
            "n_pip": len(c.get("pip") or []),
            "pkg_mgr": (c.get("os") or {}).get("pkg_mgr"),
            "errors": (c.get("errors") or [])[:5]}


@router.get("/dep-health")
def dep_health_groups(_u: dict = Depends(require_perm("read"))):
    """批 156（B10/M3）：服务端聚合——分组计数+组内分页行（880 行不全量拉取）。"""
    from src.data_platform import security_store as _st
    rows = _st.get_component_ledger()
    groups = {"yes": [], "no": [], "unknown": []}
    for r in rows:
        groups.setdefault(r.get("has_update") or "unknown", []).append(r)
    return {
        "counts": {k: len(v) for k, v in groups.items()},
        "total": len(rows),
        "rows": {k: v[:50] for k, v in groups.items()},   # 组内首 50 行，翻页后续批
    }


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
    if snap is None and not date_str:
        # 批 153：默认查询回落最近一行（每日 00:00-06:00 今日未扫的语义空窗——
        # 前端时间元素显示实际 scan_date；显式 ?date= 查无仍 404 保真）
        snap = store.get_latest_snapshot()
        if snap is not None:
            day = snap["scan_date"]
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
    # 批 155：串行化真源——锁存在性（§4.4c；前端零推断）
    try:
        from src.quant_common.redis_client import business_redis
        scan_running = bool(business_redis().get("sec:scan_lock"))
    except Exception:
        scan_running = False

    ledger_rows = store.get_component_ledger()
    ledger = {r["name"]: r["last_changed"] for r in ledger_rows if r["source"] == "pip"}
    # 批 154 补5：台账行逐行附 latest（服务端单源推导，前端零判断）：
    #   命中 outdated → 真实新版；pip 域/已知 rpm 域未命中 → 当前版（即已最新）；
    #   其余（pango/cairo/字体等无发行版坐标）→ None（前端显 —，不假声称）
    def _nm(x: str) -> str:
        return str(x).lower().replace("_", "-")

    _out_map = {_nm(o["name"]): o["latest"] for o in (snap.get("components") or {}).get("outdated") or []}
    _RPM_KNOWN = {"python", "nginx", "openssl", "postgres", "redis"}
    for _r in ledger_rows:
        _k = _nm(_r["name"])
        if _k in _out_map:
            _r["latest"] = _out_map[_k]
        elif _r["source"] == "pip" or _k in _RPM_KNOWN:
            _r["latest"] = _r["version"]
        else:
            _r["latest"] = None
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
        "findings": _enrich_findings(day, vulns_ok=snap["status"] == "ok", ledger=ledger,
                                      snap_created_at=snap["created_at"]),
        "components": snap["components"],
        "components_ledger": [
            {"source": r["source"], "name": r["name"], "version": r["version"],
             "origin": r["origin"] or "", "last_changed": r["last_changed"].isoformat(),
             "latest_version": r.get("latest_version"),
             "managed_by": r.get("managed_by"),
             "judge_class": r.get("judge_class"),
             "judge_why": r.get("judge_why") or "",
             "has_update": _hu(r.get("judge_class")),
             "stale_days": _stale_days(r)}
            for r in ledger_rows],
        "ports": snap["ports"],
        "ssh_cfg": snap["ssh_cfg"],
        "dnf": snap["dnf"],
        "headers": snap["headers"],
        "degraded_zones": degraded_zones,
        "vulns_db": (snap.get("components") or {}).get("vulns_db") or {},
        # 批 154：依赖滞后（venv 债务仪表——components 分区透传；检查不升级）
        "n_outdated": (snap.get("components") or {}).get("n_outdated"),
        "outdated": (snap.get("components") or {}).get("outdated") or [],
        "baseline_diff": baseline_diff,
        "scan_running": scan_running,
        "dep_health": _dep_health_summary(snap, ledger_rows),
    }


@router.delete("/finding")
def security_finding_delete(pkg: str = Query(...), vuln_id: str = Query(...),
                            _u: dict = Depends(require_perm("system_config"))):
    """删除已修复漏洞行（批 126 用户裁定：显式删除取代隐藏定期清理）。

    守卫在 store（last_seen < 今日才删——活跃行是当前风险证据，误删=假绿；
    三态 ok/not_found→404/active→409）。权限 system_config（admin-only 运维面，
    同 smtp_providers_delete 先例）+ 审计。
    """
    state = store.delete_fixed_finding(pkg, vuln_id, date.today())
    if state == "not_found":
        raise ApiError(404, "SECURITY_FINDING_NOT_FOUND", f"{pkg}/{vuln_id} 无此行")
    if state == "active":
        raise ApiError(409, "SECURITY_FINDING_ACTIVE",
                       f"{pkg}/{vuln_id} 仍活跃，仅已修复行可删")
    from src.data_platform.audit import audit_log
    audit_log(_u["username"], "security_finding_delete", f"{pkg}/{vuln_id}")
    return {"ok": True}


_SECURITY_HISTORY_CACHE: dict = {}   # 批126p：31 天逐行×基线 diff 真算实测 1.4s；扫描
                                     # 06:00 日更+安全页 5min 轮询——60s 缓存新鲜度绝对够

@router.delete("/summary")
def security_summary_delete(date_str: str = Query(alias="date"),
                            _u: dict = Depends(require_perm("data_sync"))):
    """删一整日安全快照（批 140：AI 历史摘要弹窗「删除条目」）。

    权限：data_sync（数据采集面写操作，与触发扫描同键）。删后清 history 缓存——
    否则分钟桶缓存会让弹窗读到已删行。
    """
    day = _parse_date(date_str)
    if not store.delete_snapshot(day):
        raise ApiError(404, "SECURITY_SUMMARY_NOT_FOUND", f"{day.isoformat()} 无此条目")
    _SECURITY_HISTORY_CACHE.clear()
    from src.data_platform.audit import audit_log
    audit_log(_u["username"], "security_summary_delete", day.isoformat())
    return {"ok": True}

@router.get("/history")
def security_history(days: int = 30, _u: dict = Depends(require_perm("read"))):
    """近 N 天逐日趋势：漏洞计数/新增/基线变更/组件变更（版本差真算）+ ai_summary 首行
    （B 区历史摘要折叠——日期+首行预览）。days 护栏 [1,31]（步 4 P1-5：>31 取 31——
    逐日行×基线 diff 的计算量与 payload 有界）。"""
    import time as _time
    now = _time.time()
    key = f"ts:{now // 60}:days:{max(1, min(days, 31))}"   # 分钟桶+days 参数分键
    hit = _SECURITY_HISTORY_CACHE.get(key)
    if hit is not None:
        return hit
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
            "ai_summary": summary,   # 批 140：弹窗表格要全文（payload 增量 ~KB 级，可接受）
        })
        prev_snap = s
    rows.reverse()   # 近日在前
    out = {"days": days, "items": rows}
    _SECURITY_HISTORY_CACHE[key] = out   # 单槽分钟桶（旧键被替换，无泄漏）
    return out


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
