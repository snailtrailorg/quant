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
    # P1-1（web-design 05 §5.3 B#3）：水位仪表数据——回撤/日亏/快照年龄（>300s=fail-closed 拒 BUY 可见）
    metrics = {}
    try:
        st = rc._get_global_state()
        metrics = {"total_drawdown": st.total_drawdown, "daily_loss": st.daily_loss,
                   "available": st.available}
    except Exception as e:
        logger.warning("risk metrics 计算失败: %s", e)
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT EXTRACT(EPOCH FROM (now() - MAX(ts))) FROM position_snapshot")
            row = cur.fetchone()
            metrics["snapshot_age_s"] = float(row[0]) if row and row[0] is not None else None
    except Exception:
        metrics["snapshot_age_s"] = None
    return {"halted": rc.is_halted(), "reason": rc.halt_reason(), "rules": rc.get_rules(),
            "metrics": metrics}


@router.get("/api/risk/log")
def risk_log_api(action: str = "", limit: int = 200,
                 payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """P1-1（06 B#2）：风控决策日志面板——拒单/覆写/放行三类可筛。"""
    with get_conn() as conn:
        sql = ("SELECT id, ts, action, symbol, rule, detail, severity FROM risk_log ")
        args = []
        if action:
            sql += "WHERE action=%s "
            args.append(action)
        sql += "ORDER BY id DESC LIMIT %s"
        args.append(min(int(limit), 1000))
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
    return {"items": [{"id": r[0], "ts": str(r[1])[:19] if r[1] else None, "action": r[2],
                       "symbol": r[3], "rule": r[4], "detail": r[5], "severity": r[6]}
                      for r in rows]}


# ——— P1-2 对账处置台（web-design 05 §5.4）：差异单结构化 + 处置端点族 ———

@router.get("/api/reconcile/issues")
def reconcile_issues_api(status: str = "",
                         payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """差异单列表（open 优先；结构化字段+处置状态持久化）。"""
    with get_conn() as conn:
        sql = ("SELECT id, symbol, issue_type, detail, broker_qty, derived_qty, status, "
               "first_seen, updated_at, handled_by, note, exempt_qty, exempt_until "
               "FROM reconcile_issue ")
        args = []
        if status:
            sql += "WHERE status=%s "
            args.append(status)
        sql += "ORDER BY status='open' DESC, updated_at DESC LIMIT 500"
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
    def _n(v):
        return float(v) if v is not None else None
    return {"items": [{"id": r[0], "symbol": r[1], "issue_type": r[2], "detail": r[3],
                       "broker_qty": _n(r[4]), "derived_qty": _n(r[5]), "status": r[6],
                       "first_seen": str(r[7])[:19] if r[7] else None,
                       "updated_at": str(r[8])[:19] if r[8] else None,
                       "handled_by": r[9], "note": r[10],
                       "exempt_qty": _n(r[11]),
                       "exempt_until": str(r[12]) if r[12] else None} for r in rows]}


def _issue_action(iid: int, status: str, payload: dict, note: str = None) -> dict:
    """处置动作公共落位（verify/ignore/exempt 共用）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE reconcile_issue SET status=%s, updated_at=now(), handled_by=%s, "
            "note=COALESCE(%s, note) WHERE id=%s RETURNING id",
            (status, payload.get("username", ""), note, iid))
        if not cur.fetchone():
            raise ApiError(404, "ISSUE_NOT_FOUND", "差异单不存在")
        conn.commit()
    return {"id": iid, "status": status}


@router.post("/api/reconcile/issues/{iid}/verify")
def reconcile_verify(iid: int, payload: dict = Depends(require_role("trader", "admin"))):
    """标记已核实（人工确认该差异为预期，如已知底仓）。"""
    return _issue_action(iid, "verified", payload)


@router.post("/api/reconcile/issues/{iid}/ignore")
def reconcile_ignore(iid: int, payload: dict = Depends(require_role("trader", "admin"))):
    """忽略本次（下次对账再现则重新 open）。"""
    return _issue_action(iid, "ignored", payload)


@router.post("/api/reconcile/issues/{iid}/exempt")
def reconcile_exempt(iid: int, body: dict, payload: dict = Depends(require_perm("user_mgmt"))):
    """登记豁免（标的级）——仅 admin（10 §1 系统策略：执行守护者不能抹平自己的差异）。

    body: {exempt_qty, exempt_until(YYYY-MM-DD), reason}——差异较豁免基准扩大须重新告警。
    """
    from datetime import date
    try:
        until = date.fromisoformat(str(body.get("exempt_until", "")))
    except ValueError:
        raise ApiError(400, "BAD_DATE", "exempt_until 须为 YYYY-MM-DD")
    qty = float(body.get("exempt_qty", 0) or 0)
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE reconcile_issue SET status='exempt', updated_at=now(), handled_by=%s, "
            "exempt_qty=%s, exempt_until=%s, note=%s WHERE id=%s RETURNING id",
            (payload.get("username", ""), qty, until, body.get("reason", ""), iid))
        if not cur.fetchone():
            raise ApiError(404, "ISSUE_NOT_FOUND", "差异单不存在")
        conn.commit()
    return {"id": iid, "status": "exempt", "exempt_qty": qty, "exempt_until": str(until)}


@router.post("/api/reconcile/manual-order")
def reconcile_manual_order(body: dict, payload: dict = Depends(require_perm("user_mgmt"))):
    """场外单登记（红队#4：底仓/手动单回流对账豁免基准，仅 admin）。"""
    from datetime import date
    sym = str(body.get("symbol", "")).strip()
    qty = float(body.get("volume", 0) or 0)
    if not sym or not qty:
        raise ApiError(400, "BAD_MANUAL_ORDER", "symbol 与 volume 必填")
    note = body.get("note", "场外单登记")
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT COALESCE(SUM(volume),0) FROM trade_log WHERE split_part(symbol,'.',1)=%s "
            "AND action='BUY'", (sym.split(".")[0],))
        derived = float(cur.fetchone()[0] or 0)
        cur = conn.execute(
            "SELECT COALESCE(SUM(snap_vol - vol),0) FROM ("
            " SELECT split_part(symbol,'.',1) s, SUM(volume) snap_vol FROM position_snapshot "
            " WHERE direction!='short' GROUP BY 1) p "
            "LEFT JOIN (SELECT split_part(symbol,'.',1) s, SUM(volume) vol FROM trade_log "
            " WHERE action='BUY' GROUP BY 1) t ON t.s=p.s WHERE p.s=%s", (sym.split(".")[0],))
        broker_extra = float(cur.fetchone()[0] or 0) if cur.rowcount else 0
        conn.execute(
            "INSERT INTO reconcile_issue (symbol, issue_type, detail, broker_qty, derived_qty, "
            "status, handled_by, note, exempt_qty) VALUES (%s,'manual_order',%s,%s,%s,'exempt',%s,%s,%s)",
            (sym.split(".")[0], note, derived + qty, derived,
             payload.get("username", ""), note, qty))
        conn.commit()
    return {"ok": True}


@router.post("/api/reconcile/reset")
def reconcile_reset(payload: dict = Depends(require_perm("user_mgmt"))):
    """清零确认（次日开盘前基线重置）——仅 admin；全部 open→accepted 基线（接受差异为新基线）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE reconcile_issue SET status='verified', updated_at=now(), handled_by=%s, "
            "note=COALESCE(note,'') || ' [reset-baseline]' WHERE status='open' RETURNING id",
            (payload.get("username", ""),))
        ids = [r[0] for r in cur.fetchall()]
        conn.commit()
    return {"reset": len(ids)}


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