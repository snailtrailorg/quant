"""风控路由：熔断开关 + 风控规则 CRUD + 三账对账 + 审计日志 + 数据完整性看板。"""
from fastapi import APIRouter, Depends
from ..auth import require_role, require_perm, audit_log
from ..errors import ApiError
from ..models import (RiskRuleReq)
from src.data_platform.db import get_conn
import logging
logger = logging.getLogger("web_api")

router = APIRouter(tags=["risk"])


@router.get("/api/risk/state")
def risk_state(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.risk_control import RiskControl
    rc = RiskControl.get()
    return {"halted": rc.is_halted(), "reason": rc.halt_reason(), "rules": rc.get_rules()}


@router.post("/api/risk/halt")
def risk_halt(payload: dict = Depends(require_perm("halt"))):
    from src.risk_control import RiskControl
    RiskControl.get().emergency_halt(f"Web:{payload['username']}")
    audit_log(payload["username"], "emergency_halt", detail="web button")
    return {"halted": True}


@router.post("/api/risk/resume")
def risk_resume(payload: dict = Depends(require_perm("resume"))):
    from src.risk_control import RiskControl
    RiskControl.get().resume()
    audit_log(payload["username"], "risk_resume")
    return {"halted": False}


@router.get("/api/risk-rules")
def list_risk_rules(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, type, params, enabled, updated_at FROM risk_rules ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "type": r[2], "params": r[3],
             "enabled": r[4], "updated_at": str(r[5]) if r[5] else None} for r in rows]


@router.get("/api/risk-rules/types")
def list_risk_rule_types(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列已注册的规则类型（前端下拉）"""
    from src.risk_control.risk_rule import _REGISTRY
    return {"types": list(_REGISTRY.keys())}


@router.post("/api/risk-rules")
def create_risk_rule(req: RiskRuleReq, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO risk_rules (name, type, params, enabled) VALUES (%s,%s,%s,%s) RETURNING id",
            (req.name, req.type, req.params, req.enabled))
        conn.commit()
    audit_log(payload["username"], "risk_rule_create", req.type)
    return {"id": cur.fetchone()[0]}


@router.post("/api/risk-rules/{rid}")
def update_risk_rule(rid: int, req: RiskRuleReq, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        conn.execute("UPDATE risk_rules SET name=%s, type=%s, params=%s, enabled=%s, updated_at=now() WHERE id=%s",
                     (req.name, req.type, req.params, req.enabled, rid))
        conn.commit()
    audit_log(payload["username"], "risk_rule_update", f"id={rid}")
    return {"ok": True}


@router.delete("/api/risk-rules/{rid}")
def delete_risk_rule(rid: int, payload: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM risk_rules WHERE id=%s", (rid,))
        conn.commit()
    audit_log(payload["username"], "risk_rule_delete", f"id={rid}")
    return {"ok": True}


@router.get("/api/reconcile")
def reconcile_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """三账对账（signal_log/order_log/trade_log 比对，同步执行）。"""
    from src.scheduler.tasks import reconcile_three_books
    return reconcile_three_books.apply().get()


@router.get("/api/audit")
def get_audit(payload: dict = Depends(require_perm("user_mgmt"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, ts, actor, action, detail FROM audit_log ORDER BY ts DESC LIMIT 100")
        rows = cur.fetchall()
    return [{"id": r[0], "ts": str(r[1]) if r[1] else None, "actor": r[2], "action": r[3], "detail": r[4]} for r in rows]


@router.get("/api/data-integrity")
def data_integrity_api(freq: str = "1D",
                      payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """数据完整性看板：每标的本地条数 vs 预期，算完整性%。

    freq: 1D（按 trade_cal 交易日）/ 1min / 5min（按自然日 × bars_per_day）。
    返回 {items:[{symbol, local_count, first, last, expected, pct, status}], summary}
    status: complete(>=99%) / partial(>0%) / missing(0)
    """
    if freq not in ("1D", "1min", "5min"):
        return {"error": "freq 必须是 1D/1min/5min"}
    table = "bar_1D" if freq == "1D" else f"bar_{freq}"
    bars_per_day = {"1D": 1, "1min": 240, "5min": 48}[freq]
    with get_conn() as conn:
        try:
            cur = conn.execute(
                f"SELECT symbol, count(*), min(ts)::date, max(ts)::date FROM {table} GROUP BY symbol ORDER BY symbol")
            rows = cur.fetchall()
        except Exception:
            return {"items": [], "summary": {"total": 0, "complete": 0, "partial": 0, "missing": 0}}
        if freq == "1D":
            cur = conn.execute("SELECT cal_date FROM trade_cal WHERE is_open=1")
            day_set = {r[0] for r in cur.fetchall()}
        else:
            day_set = None

    items = []
    complete = partial = missing = 0
    for sym, cnt, first, last in rows:
        if not first or not last or cnt == 0:
            missing += 1
            items.append({"symbol": sym, "local_count": cnt, "first": None, "last": None,
                          "expected": 0, "pct": 0, "status": "missing"})
            continue
        if freq == "1D":
            # trade_cal 可能不全（只近年同步），fallback 工作日估算取大值
            tc_count = sum(1 for d in day_set if first <= d <= last) if day_set else 0
            workday_est = (last - first).days * 5 // 7  # 每周 5 工作日粗估
            expected = max(tc_count, workday_est)
        else:
            expected = ((last - first).days + 1) * bars_per_day
        pct = round(cnt / expected * 100, 1) if expected else 0
        status = "complete" if pct >= 99 else ("partial" if pct > 0 else "missing")
        if status == "complete": complete += 1
        elif status == "partial": partial += 1
        else: missing += 1
        items.append({"symbol": sym, "local_count": cnt, "first": str(first), "last": str(last),
                      "expected": expected, "pct": pct, "status": status})
    return {"items": items, "summary": {"total": len(items), "complete": complete,
                                        "partial": partial, "missing": missing}}