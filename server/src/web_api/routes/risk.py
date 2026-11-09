"""风控路由：熔断开关 + 风控规则 CRUD + 三账对账 + 审计日志 + 数据完整性看板。"""
import logging

from fastapi import APIRouter, Body, Depends, Query

from src.data_platform.db import get_conn, get_conn_for_heavy_read

from ..auth import audit_log, require_perm
from ..errors import ApiError
from ..models import RiskRuleReq

logger = logging.getLogger("web_api")

_AGG_CACHE: dict[str, tuple[float, dict]] = {}   # 批27-26：全表聚合 60s 缓存（键含参数——integrity:{freq}）

router = APIRouter(tags=["risk"])


@router.get("/api/risk/state")
def risk_state(payload: dict = Depends(require_perm("read"))):
    from src.risk_control import RiskControl
    rc = RiskControl.get()
    # P1-1（web-design 05 §5.3 B#3）：水位仪表数据——回撤/日亏/快照年龄（>300s=fail-closed 拒 BUY 可见）
    metrics = {}
    try:
        # D2：风控水位读「默认 account」（min 交易域）——_get_global_state 已 per-account 过滤，
        # 旧 "" 无「读全局」语义（传空串按 account_id 过滤恒不命中→available=False 假「不可用」）
        with get_conn() as conn:
            row = conn.execute(
                "SELECT min(id) FROM trading_account"
            ).fetchone()
            vid = row[0] if row else None
        st = rc._get_global_state(vid)
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
                 payload: dict = Depends(require_perm("read"))):
    """P1-1（06 B#2）：风控决策日志面板——拒单/覆写/放行三类可筛。"""
    with get_conn() as conn:
        sql = ("SELECT id, ts, action, symbol, rule, detail, severity FROM risk_log ")
        args = []
        if action:
            sql += "WHERE action=%s "
            args.append(action)
        sql += "ORDER BY id DESC LIMIT %s"
        args.append(max(1, min(int(limit), 1000)))   # 批27-10：下限 1——负 limit 致 PG 500
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
    return {"items": [{"id": r[0], "ts": r[1].isoformat() if r[1] else None, "action": r[2],
                       "symbol": r[3], "rule": r[4], "detail": r[5], "severity": r[6]}
                      for r in rows]}


# ——— P1-2 对账处置台（web-design 05 §5.4）：差异单结构化 + 处置端点族 ———

@router.get("/api/reconcile/issues")
def reconcile_issues_api(status: str = "",
                         payload: dict = Depends(require_perm("read"))):
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
                       "first_seen": r[7].isoformat() if r[7] else None,
                       "updated_at": r[8].isoformat() if r[8] else None,
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
def reconcile_verify(iid: int, payload: dict = Depends(require_perm("trade"))):
    """标记已核实（人工确认该差异为预期，如已知底仓）。"""
    return _issue_action(iid, "verified", payload)


@router.post("/api/reconcile/issues/{iid}/ignore")
def reconcile_ignore(iid: int, payload: dict = Depends(require_perm("trade"))):
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
    if until < date.today():   # 批36a-2：登记即过期=误操作（B-P2-8）
        raise ApiError(400, "BAD_DATE", "豁免生效日不得早于今天")
    try:
        qty = float(body.get("exempt_qty", 0) or 0)   # 批36a-2：cast 先行（原 float 无 try）
    except (TypeError, ValueError):
        raise ApiError(400, "BAD_PARAM", "豁免数量必须是数字")
    if qty < 0:   # 批36a-2：负豁免=反向扭曲对账基准（0=零豁免语义保留）
        raise ApiError(400, "BAD_PARAM", "豁免数量不得为负（0=零豁免，任何差异重新告警）")
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
    sym = str(body.get("symbol", "")).strip()
    try:
        qty = float(body.get("volume", 0) or 0)   # 批27-10：非数字串原直接 ValueError 500
    except (TypeError, ValueError):
        raise ApiError(400, "BAD_PARAM", "volume 必须是数字")
    if not sym or not qty:
        raise ApiError(400, "BAD_MANUAL_ORDER", "symbol 与 volume 必填")
    if qty <= 0 or qty > 1e9:   # 批36a-2：负量污染对账基准；上限 1e9（场外底仓大宗可超亿股）
        raise ApiError(400, "BAD_MANUAL_ORDER", "数量须为正数（上限 10 亿）")
    note = body.get("note", "场外单登记")
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT COALESCE(SUM(volume),0) FROM trade_log WHERE split_part(symbol,'.',1)=%s "
            "AND action='BUY'", (sym.split(".")[0],))
        derived = float(cur.fetchone()[0] or 0)
        # 批39 A-P2-6：broker_extra 快照聚合查询已删（算后未用——INSERT 用 derived+qty 自洽）
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
    """批 171o：熔断=拦截+撤单串联——**先置 halted 再 dispatch cancel-all**（A-P0-1：
    先撤后停的两步窗内策略新 BUY 照常入场；halt 是 Valkey 键即时生效，先停后撤零窗口；
    与 runner 侧 halt_edge_cancel 沿撤单双保险——沿判定幂等，已熔断再点=只撤单）。"""
    from src.risk_control import RiskControl
    from src.web_api.errors import ApiError
    RiskControl.get().emergency_halt(f"Web:{payload['username']}")
    audit_log(payload["username"], "emergency_halt", detail="web button (halt then cancel-all)")
    # 2026-11-09（171n e2e）：halt 是**全局止血**——无活动 runner/撤单下发失败绝不
    # 反过来让 halt 请求失败（原实现 dispatch 抛 409 时 halted 已置位，前端却收到失败=
    # 状态与回执矛盾）。撤单降级为 best-effort，结果随回执带回。
    try:
        cancel_out = _dispatch_risk_cmd("cancel-all", payload)
    except ApiError as e:
        cancel_out = {"error": e.code, "detail": str(e)}
    except Exception as e:   # noqa: BLE001 —— halt 永不因撤单失败而失败
        cancel_out = {"error": "CANCEL_DISPATCH_FAILED", "detail": str(e)[:200]}
    return {"halted": True, "cancel": cancel_out}


@router.post("/api/risk/resume")
def risk_resume(payload: dict = Depends(require_perm("resume"))):
    from src.risk_control import RiskControl
    RiskControl.get().resume()
    audit_log(payload["username"], "risk_resume")
    return {"halted": False}


def _hb_get(hb, key: str):
    """心跳字段读取（str/bytes 双形态）——decode_responses 缺省 False 时键是 bytes，
    直接 hb.get("ts") 恒 None（171n e2e 抓到的 409 真因）。"""
    if not isinstance(hb, dict):
        return None
    if key in hb:
        return hb[key]
    return hb.get(key.encode())


def _live_runners(accounts: list[int] | None = None) -> list[tuple[str, int]]:
    """批 171n：活 runner 枚举——live_task status='running' 行 ∩ 心跳键新鲜（B-P0-2：
    td_registry 是 builder 插件表非运行时枚举；僵尸 runner〔行 running 心跳死〕不收——
    命令下发给死进程=静默过期）。返回 [(tid, account_id)]。
    批 171o：accounts 过滤（勾选集——None=全部；过滤后空=调用方区分 400/409）。"""
    from src.quant_common.redis_client import business_redis
    from src.data_platform.config_store import live_running_tasks
    out = []
    rows = live_running_tasks()
    if accounts is not None:
        rows = [(tid, acct) for tid, acct in rows if acct in set(accounts)]
    try:
        # 2026-11-09（171n e2e 抓真因）：business_redis 默认 decode_responses=False
        # ⇒ hgetall 返回 bytes 键，`hb.get("ts")` 恒 None ⇒ 所有 runner 被判「心跳死」
        # ⇒ cancel-all/close-all/halt 恒 409（mock 用 str-key dict 全绿掩盖）。
        # 修：decode_responses=True + _hb_get 双形态容错（旧 client 复用也不炸）。
        r = business_redis(decode_responses=True, socket_timeout=3)
        import time as _t
        for tid, acct in rows:
            try:
                hb = r.hgetall(f"quant:hb:task:{tid}")
                ts = _hb_get(hb, "ts")
                if ts and _t.time() - float(ts) < 30:
                    out.append((str(tid), acct))
            except Exception:
                continue
    except Exception:
        pass   # Valkey 不可达：返回空=端点侧 409（保守——不发命令给未知态）
    return out


def _dispatch_risk_cmd(verb: str, payload: dict, accounts: list[int] | None = None) -> dict:
    """批 171n：急停命令下发共用体（cancel-all/close-all 两端点同链）。

    三态分叉（A-P1-3）：无 runner ⇒ 409；已下发未回执（10s 窗——171o A-P2-1：
    close-all 串联〔撤单+稳定窗+逐标发单〕5s 不够）⇒ 202 dispatched；
    回执在窗内 ⇒ 200 汇总。audit_log 三态分别落痕。
    批 171o：accounts 勾选集过滤+**按 account 去重**（A-P1-4——撤/平是网关全账户
    动作，同 account 多 runner 双发重复；每 account 取首个活 tid）；过滤后空集
    （有活 runner 但勾选不含）=400 RISK_NO_MATCH_ACCOUNT 区别于 409。
    """
    import json as _json
    import time as _t
    import uuid as _uuid
    from src.web_api.errors import ApiError
    from src.quant_common.redis_client import business_redis
    from src.data_platform.config_store import live_running_tasks
    if accounts is not None:
        _all_rows = live_running_tasks()
        _valid = {a for _t_, a in _all_rows}
        _bad = set(accounts) - _valid
        if _bad:
            raise ApiError(400, "RISK_INVALID_ACCOUNT",
                           f"无效账号 id: {sorted(_bad)}（不在 running 任务集）")
    runners = _live_runners(accounts=accounts)
    if not runners:
        if accounts is not None and live_running_tasks():
            raise ApiError(400, "RISK_NO_MATCH_ACCOUNT",
                           "勾选账号无活动 runner（其余账号有活任务但不在勾选集）")
        audit_log(payload["username"], f"risk_{verb.replace('-', '_')}",
                  detail="no active runner (409)")
        raise ApiError(409, "RISK_NO_ACTIVE_RUNNER",
                       "无活动实盘 runner（live_task running ∩ 心跳新鲜为空）——命令未下发")
    # 批 171o（A-P1-4）：按 account 去重——同 account 多 runner 只发一 tid（撤/平是
    # 网关全账户动作，双发重复撤单）。
    _seen_accts = set()
    runners = [(tid, acct) for tid, acct in runners
               if not (acct in _seen_accts or _seen_accts.add(acct))]
    r = business_redis(socket_timeout=3)
    req_ids = []
    for tid, _acct in runners:
        req_id = _uuid.uuid4().hex[:12]
        r.set(f"risk:cmd:{tid}",
              _json.dumps({"id": req_id, "verb": verb,
                           "operator": payload["username"], "ts": _t.time()}),
              ex=10 * 60)
        req_ids.append((tid, req_id))
    # 回执窗（5s——worker 拍周期 1s 轮询+执行）
    results = {}
    deadline = _t.time() + 5
    pending = {req_id: tid for tid, req_id in req_ids}
    while pending and _t.time() < deadline:
        for req_id in list(pending):
            raw = r.get(f"risk:cmd:result:{req_id}")
            if raw:
                results[pending.pop(req_id)] = _json.loads(raw)
                r.delete(f"risk:cmd:result:{req_id}")
        _t.sleep(0.3)
    audit_log(payload["username"], f"risk_{verb.replace('-', '_')}",
              detail=f"dispatched={len(req_ids)} acked={len(results)}")
    if pending:
        return {"status": "dispatched", "dispatched": len(req_ids),
                "acked": len(results), "results": results,
                "pending": list(pending.values())}
    return {"status": "done", "dispatched": len(req_ids), "results": results}


@router.get("/api/risk/exposure")
def risk_exposure(_p: dict = Depends(require_perm("halt"))):
    """批 171o：紧急处置页火势总览——按通道账号聚合（A-P1-3 改道：PG 真相表+HB 扩展，
    不新建快照钩子）。行=trading_account enabled 账号：
    - working_orders：HB 键 quant:hb:task:{tid} 扩展字段（5s 拍新鲜；-1=TD 断线未知）
    - 持仓市值/盈亏：position_snapshot/account_snapshot（snapshot_cycle 60s 真值；
      ts>120s=stale ⇒ None〔FE 渲染「未知」——A-P0-2：断线残影防假清仓〕）
    - running：活 runner 在场与否（live_running_tasks ∩ 心跳）
    页脚口径：在场单数 5s 快照；持仓市值 60s 快照（>120s 显未知）。
    """
    import time as _t
    from src.quant_common.redis_client import business_redis
    from src.data_platform.config_store import (exposure_snapshots, live_running_tasks,
                                                trading_accounts_with_flags)
    live = {str(tid): acct for tid, acct in live_running_tasks()}
    snaps = exposure_snapshots()
    # 批 171r：**不设兜底并集**——「任务必引用在册交易账号」由 DB 触发器+创建校验
    # 从根本保证（live_task.account_id FK + 0174 触发器：mode=live 禁虚拟账号）。
    # 表与 FK 已是不变量真源，此处直读交易域账号（is_virtual=false），零兜底逻辑。
    _rows = trading_accounts_with_flags()
    accts = [r[:4] for r in _rows if not r[4]]
    r = None
    try:
        r = business_redis(decode_responses=True, socket_timeout=3)   # 同 _live_runners：bytes 键防呆
    except Exception:
        pass
    now = _t.time()
    items = []
    for acct_id, name, provider, acct_enabled in accts:
        working = None
        running = False
        if r is not None:
            for tid, acct in live.items():
                if acct != acct_id:
                    continue
                try:
                    hb = r.hgetall(f"quant:hb:task:{tid}")
                    ts = _hb_get(hb, "ts")
                    # 心跳新鲜才算「运行中」（僵尸 runner：行 running 但心跳死——
                    # 171n e2e 抓到 dead runner 仍显示 running=True，误导处置人）
                    running = bool(ts) and (now - float(ts) < 30)
                    wo = _hb_get(hb, "working_orders")
                    working = int(wo) if (running and wo is not None) else None
                except Exception:
                    pass
                break
        sn = snaps.get(acct_id) or {}
        ts_fresh = sn.get("pos_ts")
        stale = (ts_fresh is None) or (now - ts_fresh.timestamp() > 120)
        snap = sn.get("snap")
        items.append({
            "account_id": acct_id, "name": name, "provider": provider,
            "enabled": bool(acct_enabled),   # 批 171q：未启用=FE 灰态不可勾
            "running": running,
            "working_orders": working,          # None=无活 runner/键缺（FE 显 —）
            "position_value": None if stale else sn.get("pos_value", 0.0),
            "daily_pnl": (None if not snap or (now - snap[2].timestamp() > 120)
                          else snap[1]),
            "stale": stale,
        })
    return {"items": items, "halted_ts": RiskControl_halt_ts()}


def RiskControl_halt_ts():
    from src.risk_control.risk import RiskControl
    rc = RiskControl.get()
    try:
        return rc.halt_ts()
    except Exception:
        return None


@router.post("/api/risk/cancel-all")
def risk_cancel_all(body: dict = Body(default={}),
                   payload: dict = Depends(require_perm("halt"))):
    """批 171n：一键撤全部在场委托（「请求已受理」语义——XTP 异步，结果见 journal）。
    批 171o：可选 body accounts=[id]（勾选集——缺省全部）。

    2026-11-09（e2e 抓真因）：原实现从 **auth payload** 取 accounts（Depends 注入的是
    用户名/角色，不是请求体）⇒ 勾选集恒 None（永远全量）。改为 Body 读。
    """
    return _dispatch_risk_cmd("cancel-all", payload, accounts=(body or {}).get("accounts"))


@router.post("/api/risk/close-all")
def risk_close_all(body: dict = Body(default={}),
                  payload: dict = Depends(require_perm("halt"))):
    """批 171n：一键全平（body {"confirm":"CONFIRM"} 二次确认——股灾场景防误触）。
    批 171o：清仓=全包含（用户裁定四级递进）——未熔断先置 halted（拒新单）→撤在场单→
    全持仓平仓。worker 侧 consume_risk_cmd 的 close-all 分支已串联撤+平（171o）；web 侧
    本端点只补前置 halt。"""
    # 2026-11-09（e2e 抓真因）：confirm/accounts 原从 auth payload 取 ⇒ 恒 400
    # （前端 body 里的 CONFIRM 被无视，用户「手动平仓平不掉」的真根因）。改 Body 读。
    if (body or {}).get("confirm") != "CONFIRM":
        from src.web_api.errors import ApiError
        raise ApiError(400, "RISK_CONFIRM_REQUIRED",
                       "close-all 需 body {\"confirm\":\"CONFIRM\"} 二次确认")
    from src.risk_control import RiskControl
    rc = RiskControl.get()
    if not rc.is_halted():
        rc.emergency_halt(f"close-all:{payload['username']}")
        audit_log(payload["username"], "emergency_halt", detail="auto (close-all preamble)")
    return _dispatch_risk_cmd("close-all", payload, accounts=(body or {}).get("accounts"))


@router.get("/api/risk-rules")
def list_risk_rules(payload: dict = Depends(require_perm("read"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, type, params, enabled, updated_at FROM risk_rules ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "type": r[2], "params": r[3],
             "enabled": r[4], "updated_at": str(r[5]) if r[5] else None} for r in rows]


@router.get("/api/risk-rules/types")
def list_risk_rule_types(payload: dict = Depends(require_perm("read"))):
    """列规则类型+参数 schema 元数据（批36a-1 用户裁定=活参数入 UI：schema 单源下发驱动
    动态表单——前端纯渲染零硬编码；active=False=registry 三类存储未生效，前端标注）。"""
    from src.risk_control.risk_schema import RISK_PARAM_SCHEMA
    return {"types": [
        {"type": t, "active": spec["active"], "params": spec["params"]}
        for t, spec in RISK_PARAM_SCHEMA.items()
    ]}


@router.post("/api/risk-rules")
def create_risk_rule(req: RiskRuleReq, payload: dict = Depends(require_perm("risk_rules"))):
    from src.risk_control.risk_schema import validate_params
    clean, err = validate_params(req.type, req.params)   # 批36a-1：type 白名单+JSON+逐键查表
    if err:
        raise ApiError(400, "RISK_PARAM_INVALID", err)
    import json as _json
    params_json = _json.dumps(clean)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO risk_rules (name, type, params, enabled) VALUES (%s,%s,%s,%s) RETURNING id",
            (req.name, req.type, params_json, req.enabled))
        conn.commit()
    audit_log(payload["username"], "risk_rule_create", req.type)
    return {"id": cur.fetchone()[0]}


@router.post("/api/risk-rules/{rid}")
def update_risk_rule(rid: int, req: RiskRuleReq, payload: dict = Depends(require_perm("risk_rules"))):
    from src.risk_control.risk_schema import validate_params
    clean, err = validate_params(req.type, req.params)
    if err:
        raise ApiError(400, "RISK_PARAM_INVALID", err)
    import json as _json
    params_json = _json.dumps(clean)
    with get_conn() as conn:
        conn.execute("UPDATE risk_rules SET name=%s, type=%s, params=%s, enabled=%s, updated_at=now() WHERE id=%s",
                     (req.name, req.type, params_json, req.enabled, rid))
        conn.commit()
    audit_log(payload["username"], "risk_rule_update", f"id={rid}")
    return {"ok": True}


@router.delete("/api/risk-rules/{rid}")
def delete_risk_rule(rid: int, payload: dict = Depends(require_perm("risk_rules"))):
    with get_conn() as conn:
        conn.execute("DELETE FROM risk_rules WHERE id=%s", (rid,))
        conn.commit()
    audit_log(payload["username"], "risk_rule_delete", f"id={rid}")
    return {"ok": True}


@router.get("/api/reconcile")
def reconcile_api(payload: dict = Depends(require_perm("read"))):
    """三账对账（signal_log/order_log/trade_log 比对，同步执行）。"""
    from src.scheduler.tasks import reconcile_three_books
    out = reconcile_three_books.apply().get()
    return out


@router.get("/api/audit")
def get_audit(before: str | None = None, limit: int = 100,
              actor: list[str] = Query(default=[]), action: list[str] = Query(default=[]),
              payload: dict = Depends(require_perm("user_mgmt"))):
    """审计日志。批32：游标分页（每页 100，ORDER BY ts DESC, id DESC 双键——同秒多行单键丢/重行）
    +actor/action 筛选后端化（Audit.vue 原两维前端筛同病同治）+首屏附带 actors/actions 全量下拉源
    （DISTINCT LIMIT 500 封顶——audit_log 只插不删，全表扫随年头增长，量级注记）。
    **响应形状变更 list→dict（破坏性）**：唯一消费方 Audit.vue 同批切换+Ansible 同车发布无错配窗口。
    批16 bug1 的 target 列保留。"""
    limit = max(1, min(limit, 500))
    where, params = "", []
    if before:
        try:
            _us, _rid = before.split("|")
            from datetime import datetime as _dt
            from datetime import timezone as _tz
            _ts = _dt.fromtimestamp(int(_us) / 1_000_000, tz=_tz.utc)
            where += " WHERE (ts, id) < (%s, %s)"
            params += [_ts, int(_rid)]
        except Exception:
            raise ApiError(400, "CURSOR_INVALID", "游标无效或已过期，请刷新列表")
    _ac = [a for a in actor if a]   # B-P2-2：全空串维度跳过
    if _ac:
        where += (" AND" if where else " WHERE") + " actor = ANY(%s)"
        params.append(_ac)
    _at = [a for a in action if a]
    if _at:
        where += (" AND" if where else " WHERE") + " action = ANY(%s)"
        params.append(_at)
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT id, ts, actor, action, target, detail FROM audit_log{where} "
            "ORDER BY ts DESC, id DESC LIMIT %s", (*params, limit))
        rows = cur.fetchall()
        actors, actions = [], []
        if not before:   # 首屏下拉源（翻页不重复）
            acur = conn.execute("SELECT DISTINCT actor FROM audit_log ORDER BY 1 LIMIT 500")
            actors = [r[0] for r in acur.fetchall()]
            tcur = conn.execute("SELECT DISTINCT action FROM audit_log ORDER BY 1 LIMIT 500")
            actions = [r[0] for r in tcur.fetchall()]
    # A-P2-1 注记：ts [:19] 截断对齐 /api/log 既有口径——非 +08 浏览器显示会漂移（fmtTime 按本地解析）
    logs = [{"id": r[0], "ts": r[1].isoformat() if r[1] else None, "actor": r[2], "action": r[3],
             "target": r[4], "detail": r[5]} for r in rows]
    next_cursor = None
    if len(rows) == limit and rows:
        last = rows[-1]
        next_cursor = f"{int(last[1].timestamp() * 1_000_000)}|{last[0]}"
    return {"logs": logs, "next": next_cursor, "actors": actors, "actions": actions}





@router.get("/api/data-integrity")
def data_integrity_api(freq: str = "1D",
                      payload: dict = Depends(require_perm("read"))):
    """数据完整性看板：每标的本地条数 vs 预期，算完整性%。

    freq: 1D / 1min / 5min。预期根数按**市场**算（批170）：A 股/场内基金走 trade_cal 交易日
    × 240/48 根；加密（BINANCE/OKX）24/7 × 1440/288 根——口径真源 data_platform.integrity。
    返回 {items:[{symbol, local_count, first, last, expected, pct, status}], summary}
    status: complete(>=99%) / partial(>0%) / missing(0)
    批27-26：全表聚合按 freq 分键 60s 缓存（_METRICS_CACHE 先例——viewer 可反复刷的 DB 压力点）。
    """
    if freq not in ("1D", "1min", "5min"):
        return {"error": "freq 必须是 1D/1min/5min"}
    import time as _t
    _ck = f"integrity:{freq}"
    _hit = _AGG_CACHE.get(_ck)
    if _hit and _t.time() - _hit[0] < 60:
        return _hit[1]
    table = "bar_1D" if freq == "1D" else f"bar_{freq}"
    from src.data_platform.integrity import expected_bars   # 批170：按市场口径（加密 24/7）
    try:
        # 批 91：① 时区转换移出聚合——min/max 直接作用于索引列 ts（dev 1371 万行实测
        #          1.87s→1.00s）；② 只读全表聚合放宽 statement_timeout（prod 10s 过紧）。
        with get_conn_for_heavy_read() as conn:
            cur = conn.execute(
                f"SELECT symbol, count(*), (min(ts) AT TIME ZONE 'Asia/Shanghai')::date, "
                f"(max(ts) AT TIME ZONE 'Asia/Shanghai')::date FROM {table} GROUP BY symbol ORDER BY symbol")
            rows = cur.fetchall()
            # 批170：取有序开市日（完整性口径按市场分别算预期——见 data_platform.integrity）
            cur = conn.execute("SELECT cal_date FROM trade_cal WHERE is_open=1 ORDER BY cal_date")
            day_set = [r[0] for r in cur.fetchall()]
    except Exception as e:
        # 批 91：不再静默吞错。此前该分支返回的体与「表真的空」**逐字相同** ⇒ 界面把
        # 「查询失败」谎报成「0 只标的 / 暂无数据」，前端错误条（loadFailed）永不亮。
        logger.exception("data-integrity 查询失败（freq=%s table=%s）", freq, table)
        return {"items": [], "summary": {"total": 0, "complete": 0, "partial": 0, "missing": 0},
                "error": f"{type(e).__name__}: {str(e)[:160]}"}

    items = []
    complete = partial = missing = 0
    # 批 171b：停牌/退市净化——scope 级两次批量（零 N+1）：①停牌图 ②SM delist（下沉
    # integrity.py，路由零内联 SQL 铁律）。键均 vt 形态（口内归一），与下方循环变量 sym
    # 直接 get；crypto 由 expected_bars 内部跳过停牌参。
    from datetime import date as _d
    from src.data_platform.integrity import delist_map_bulk, is_crypto, suspend_days_bulk
    _vt_syms = {r[0] for r in rows}
    _non_crypto = {s for s in _vt_syms if not is_crypto(s)}
    # 停牌批量窗下界＝面板行的最早 first（r[2]——列序 symbol/count/first/last；步4 B-P0-1：
    # 误用 r[1](count) 会 int 入参被 fail-open 吞成静默全灭）
    _susp = (suspend_days_bulk(min((r[2] for r in rows if r[2]), default=_d(2000, 1, 1)),
                               _d.today(), _non_crypto)
             if _non_crypto else {})
    _delist = delist_map_bulk()
    _suspended_total = 0   # 批 171j：面板级净化规模（Σ 已扣停牌日数）
    for sym, cnt, first, last in rows:
        if not first or not last or cnt == 0:
            missing += 1
            items.append({"symbol": sym, "local_count": cnt, "first": None, "last": None,
                          "expected": 0, "pct": 0, "status": "missing",
                          # 批 171j：净化对照三态契约（crypto/missing=0——规格 §0 件三）
                          "suspended_days": 0, "raw_expected": 0})
            continue
        # 批 171b：退市截断（delist 后不算期望——防「已退市仍虚增分母」）——截断只作用于
        # 期望计算的**局部量** `_last_eff`；items 的 last 字段保持原始末根日（前端
        # DataIntegrity.vue 直接展示 last，语义=末根而非退市日——步4 B-P2-1）。
        _dl = _delist.get(sym)
        _last_eff = min(last, _dl) if (_dl is not None and _dl < last) else last
        if _last_eff < first:
            missing += 1
            items.append({"symbol": sym, "local_count": cnt, "first": first, "last": last,
                          "expected": 0, "pct": 0, "status": "missing",
                          "suspended_days": len(_susp.get(sym) or []),
                          "raw_expected": 0})
            continue
        # 批170 根因修：按市场算预期（旧口径 max(交易日, 自然日×5/7) 使 A 股天花板 ~93%，
        # 且把加密 7×24 用 A 股日历当分母 → 实测 150%+；分钟线同理按 240 而非 1440）
        expected = expected_bars(freq, sym, first, _last_eff, day_set,
                                 day_set[0] if day_set else None,
                                 day_set[-1] if day_set else None,
                                 suspend_days=_susp.get(sym))
        # 批 171j：净化对照列——raw_expected=不减停牌的期望（同参 suspend_days=None）。
        # pct 语义不动（唯一真源=净化后收敛度）；对照列让「87% 分母扣了多少停牌」可见。
        # crypto 的 expected_bars 内部跳过停牌参 ⇒ raw==expected 天然成立（无对照语义）。
        _susp_n = len(_susp.get(sym) or [])
        _suspended_total += _susp_n
        raw_expected = expected_bars(freq, sym, first, _last_eff, day_set,
                                     day_set[0] if day_set else None,
                                     day_set[-1] if day_set else None,
                                     suspend_days=None)
        pct = round(cnt / expected * 100, 1) if expected else 0
        status = "complete" if pct >= 99 else ("partial" if pct > 0 else "missing")
        if status == "complete":
            complete += 1
        elif status == "partial":
            partial += 1
        else:
            missing += 1
        items.append({"symbol": sym, "local_count": cnt, "first": str(first), "last": str(last),
                      "expected": expected, "pct": pct, "status": status,
                      "suspended_days": _susp_n, "raw_expected": raw_expected})
    _result = {"items": items, "summary": {"total": len(items), "complete": complete,
                                        "partial": partial, "missing": missing,
                                        # 批 171j：面板级净化规模（Σ 已扣停牌日数）
                                        "suspended_total": _suspended_total}}
    _AGG_CACHE[_ck] = (_t.time(), _result)   # 批27-26：按 freq 分键缓存
    return _result
