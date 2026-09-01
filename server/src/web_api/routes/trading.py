import json, subprocess, time
from fastapi import APIRouter, Depends, Request, Body, Header, HTTPException, Query
from ..auth import require_role, require_perm, audit_log
from ..errors import ApiError
from ..models import (LoginReq, UserCreate, StrategyConfig, InviteReq, RegisterReq, ForgotReq, ResetReq, ChangePwdReq, LogAnalyzeReq, ChatReq, LLMModelReq, IMBotCreateReq, IMBotUpdateReq, IMBotUserReq, LlmBudgetReq, DataSourceReq, ChannelReq, BrokerReq, RiskRuleReq, PoolReq, StrategyAccountReq)
from src.data_platform.db import get_conn
import logging
logger = logging.getLogger("web_api")

router = APIRouter(tags=["trading"])

LIVE_TRADING_MARKETS = ("convertible", "etf", "astock", "binance_perp", "okx_perp")


@router.get("/api/live-task")
def list_live_tasks(status: str | None = None,
                    payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列实盘任务。"""
    with get_conn() as conn:
        if status:
            cur = conn.execute(
                "SELECT id, name, strategy_id, symbol, params, status, account_id, initial_capital, created_at "
                "FROM live_task WHERE status=%s ORDER BY id DESC", (status,))
        else:
            cur = conn.execute(
                "SELECT id, name, strategy_id, symbol, params, status, account_id, initial_capital, created_at "
                "FROM live_task ORDER BY id DESC")
        rows = cur.fetchall()
    # P1-5（web-design 05 §5.8/06 B#5）：合并 worker 心跳（md_mode/lag/bars/frozen/gen）——
    # 任务"活着吗、行情新鲜吗、冻没冻"三问列表页直答
    hb = {}
    try:
        import redis as _redis, os as _os
        r_ = _redis.Redis.from_url(_os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/4"),
                                   decode_responses=True, socket_timeout=1)
        for rid, *_ in rows:
            h = r_.hgetall(f"quant:hb:task:{rid}")
            if h:
                hb[rid] = h
    except Exception:
        pass
    out = []
    for r in rows:
        h = hb.get(r[0], {})
        out.append({"id": r[0], "name": r[1], "strategy_id": r[2], "symbol": r[3],
                    "params": json.loads(r[4]) if isinstance(r[4], str) else (r[4] or {}),
                    "status": r[5], "account_id": r[6], "initial_capital": float(r[7]) if r[7] else None,
                    "created_at": str(r[8]) if r[8] else None,
                    "md_mode": (h.get("md") if h else None) or (json.loads(r[4]) if isinstance(r[4], str) else (r[4] or {})).get("md_mode") or "hub",
                    "lag": float(h["lag"]) if h.get("lag") not in (None, "", "-1") else (float(h["lag"]) if h.get("lag") == "-1" else None),
                    "bars": int(h["bars"]) if h.get("bars") else 0,
                    "frozen": h.get("frozen") == "1",
                    "hb_age_s": (time.time() - float(h["ts"])) if h.get("ts") else None})
    return out


@router.post("/api/live-task")
def create_live_task(body: dict = Body(...),
                     payload: dict = Depends(require_perm("strategy_control"))):
    """创建实盘任务：选策略+标的+任务参数值。创建时构建 strategy_snapshot。"""
    from src.strategy_framework.strategy import (
        validate_parameter_defs, validate_params_against_defs, build_default_params
    )
    name = body.get("name", "")
    strategy_id = body.get("strategy_id", "")
    symbol = body.get("symbol", "")
    params = body.get("params", {})
    account_id = body.get("account_id")
    initial_capital = body.get("initial_capital", 1000000)

    if not name or not strategy_id or not symbol:
        raise ApiError(400, "MISSING_FIELDS", "name/strategy_id/symbol 必填")

    # 读策略配置
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, name, type, symbol, adapter, enabled, factors, aggregator, risk, params, backtest_verified "
            "FROM strategy_config WHERE id=%s", (strategy_id,))
        row = cur.fetchone()
    if not row:
        raise ApiError(404, "STRATEGY_NOT_FOUND", f"策略 {strategy_id} 不存在")
    if not row[10]:
        raise ApiError(403, "STRATEGY_NOT_VERIFIED", "策略未通过回测验证，禁止实盘")

    sc_params = json.loads(row[9]) if isinstance(row[9], str) else (row[9] or {})
    defs = sc_params.get("parameter_defs", [])

    # 校验参数定义
    err = validate_parameter_defs(defs)
    if err:
        raise ApiError(400, "PARAM_DEFS_INVALID", f"策略参数定义错误: {err}")

    # 合并默认值 + 用户传入参数
    merged_params = {**build_default_params(defs), **params}
    err = validate_params_against_defs(merged_params, defs)
    if err:
        raise ApiError(400, "PARAM_INVALID", f"参数值错误: {err}")

    # 构建策略快照（创建时固化，后续改策略不影响）
    strategy_snapshot = {
        "id": row[0], "name": row[1], "type": row[2],
        "adapter": row[4], "factors": row[6] if isinstance(row[6], list) else json.loads(row[6] or "[]"),
        "aggregator": row[7] if isinstance(row[7], dict) else json.loads(row[7] or "{}"),
        "risk": row[8] if isinstance(row[8], dict) else json.loads(row[8] or "{}"),
        "params": sc_params,  # 含 mode/python_code/parameter_defs
    }

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO live_task (name, strategy_id, symbol, params, strategy_snapshot, status, "
            "account_id, initial_capital) VALUES (%s,%s,%s,%s,%s,'pending',%s,%s) RETURNING id",
            (name, strategy_id, symbol, json.dumps(merged_params), json.dumps(strategy_snapshot),
             account_id, initial_capital))
        task_id = cur.fetchone()[0]
        conn.commit()
    audit_log(payload["username"], "create_live_task", f"task {task_id} strategy={strategy_id} symbol={symbol}")
    return {"id": task_id, "status": "pending"}


@router.post("/api/live-task/{tid}/start")
def start_live_task(tid: int, payload: dict = Depends(require_perm("strategy_control"))):
    """启动实盘任务。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT status, strategy_id FROM live_task WHERE id=%s", (tid,))
        row = cur.fetchone()
        if not row:
            raise ApiError(404, "LIVE_TASK_NOT_FOUND", "实盘任务不存在")
        conn.execute("UPDATE live_task SET status='running', updated_at=now() WHERE id=%s", (tid,))
        conn.commit()
    audit_log(payload["username"], "start_live_task", f"task {tid}")
    try:
        subprocess.run(["systemctl", "start", f"quant-live-task@{tid}"], timeout=10, capture_output=True)
    except Exception:
        logger.error("start_live_task: systemctl start quant-live-task@%s 失败", tid, exc_info=True)
    return {"id": tid, "status": "running"}


@router.post("/api/live-task/{tid}/stop")
def stop_live_task(tid: int, payload: dict = Depends(require_perm("strategy_control"))):
    """停止实盘任务。"""
    with get_conn() as conn:
        conn.execute("UPDATE live_task SET status='stopped', updated_at=now() WHERE id=%s", (tid,))
        conn.commit()
    audit_log(payload["username"], "stop_live_task", f"task {tid}")
    try:
        subprocess.run(["systemctl", "stop", f"quant-live-task@{tid}"], timeout=10, capture_output=True)
    except Exception:
        logger.error("stop_live_task: systemctl stop quant-live-task@%s 失败", tid, exc_info=True)
    return {"id": tid, "status": "stopped"}


@router.delete("/api/live-task/{tid}")
def delete_live_task(tid: int, payload: dict = Depends(require_perm("strategy_control"))):
    """删除实盘任务（仅 stopped/error 可删）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT status FROM live_task WHERE id=%s", (tid,))
        row = cur.fetchone()
        if not row:
            raise ApiError(404, "LIVE_TASK_NOT_FOUND", "实盘任务不存在")
        if row[0] == "running":
            raise ApiError(400, "LIVE_TASK_RUNNING", "运行中的任务不可删除，请先停止")
        conn.execute("DELETE FROM live_task WHERE id=%s", (tid,))
        conn.commit()
    audit_log(payload["username"], "delete_live_task", f"task {tid}")
    return {"ok": True}


@router.get("/api/live-trading")
def list_live_trading(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列实盘分项开关 + .env 总闸状态。三级 AND：总闸 AND 分项 AND 策略 enabled。"""
    from src.data_platform.settings import is_live_trading_enabled
    with get_conn() as conn:
        cur = conn.execute("SELECT market, enabled, updated_at FROM live_trading_config ORDER BY market")
        rows = cur.fetchall()
    return {
        "master_enabled": is_live_trading_enabled(),
        "items": [{"market": r[0], "enabled": r[1], "updated_at": r[2]} for r in rows],
    }


@router.post("/api/live-trading/{market}")
def update_live_trading(market: str, enabled: bool = Query(...),
                        payload: dict = Depends(require_perm("live_trading_control"))):
    """开/关某品种实盘分项（trader/admin）。需 .env 总闸也开才真生效。"""
    if market not in LIVE_TRADING_MARKETS:
        raise HTTPException(400, f"未知市场: {market}，可选: {LIVE_TRADING_MARKETS}")
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE live_trading_config SET enabled=%s, updated_at=now() WHERE market=%s RETURNING enabled",
            (enabled, market))
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, f"市场不存在: {market}")
    audit_log(payload["username"], "live_trading_toggle", detail=f"{market}={enabled}")
    return {"market": market, "enabled": row[0]}


@router.get("/api/position")
def get_position(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """当前持仓（ST2：券商 position_snapshot 快照=真相源；trade_log 推导已挪 /api/reconcile 归因）。

    stale 语义（N-S5）：position_refresh.ts 距今 >600s 或从未写过 → stale=True——
    "停更/从未跑过"≠"空仓"（空仓=refresh 新鲜且 rows=0），前端可据 stale 标注陈旧。
    """
    import datetime as _pdt
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM account_snapshot LIMIT 1")
        except Exception:
            logger.warning("get_position: account_snapshot 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT total_value, daily_pnl, initial_capital FROM account_snapshot ORDER BY ts DESC LIMIT 1")
        snap = cur.fetchone()
        # #10 口径修正（2026-08-22）：initial=账户首条快照净值（数据基线）。原读
        # initial_capital 列（live_task 策略级配置资金，默认 100 万）与账户级 total_value
        # （如测试账户 10 亿）错配 -> total_pnl 虚增 9.99 亿。列值仅作无历史时兜底。
        cur = conn.execute("SELECT total_value FROM account_snapshot ORDER BY ts ASC LIMIT 1")
        first = cur.fetchone()
        refresh_ts, refresh_rows = None, 0
        positions = []
        try:
            cur = conn.execute("SELECT ts, rows FROM position_refresh ORDER BY ts DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                refresh_ts, refresh_rows = row[0], row[1]
            cur = conn.execute(
                "SELECT symbol, direction, volume, frozen, cost_price, pnl FROM position_snapshot "
                "WHERE volume != 0")
            positions = [{"symbol": r[0], "direction": r[1], "volume": int(r[2]),
                          "frozen": int(r[3] or 0),
                          "cost_price": float(r[4]) if r[4] is not None else None,
                          "pnl": float(r[5]) if r[5] is not None else None}
                         for r in cur.fetchall()]
        except Exception:
            logger.warning("get_position: position_snapshot 未就绪（需 alembic 0043 + 任务运行）")
    stale = True
    if refresh_ts is not None:
        ts_aware = refresh_ts if refresh_ts.tzinfo else refresh_ts.replace(tzinfo=_pdt.timezone.utc)
        stale = (_pdt.datetime.now(_pdt.timezone.utc) - ts_aware).total_seconds() > 600
    total_value = float(snap[0]) if snap else 0
    initial = float(first[0]) if first and first[0] else (float(snap[2]) if snap and snap[2] is not None else 1000000)
    total_pnl = (total_value - initial) if snap else 0
    return {"positions": positions, "total_value": total_value, "total_pnl": total_pnl,
            "total_pnl_pct": round(total_pnl/initial*100, 2) if initial else 0,
            "snapshot_ts": str(refresh_ts)[:19] if refresh_ts else None,
            "snapshot_rows": refresh_rows, "stale": stale}


@router.get("/api/pnl")
def get_pnl(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """盈亏曲线（account_snapshot 时间序列，#6）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM account_snapshot LIMIT 1")
        except Exception:
            logger.warning("get_pnl: account_snapshot 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT ts, total_value, daily_pnl, initial_capital FROM account_snapshot ORDER BY ts DESC LIMIT 90")
        rows = cur.fetchall()
        # #10 口径修正（2026-08-22）：initial=账户首条快照净值（数据基线），列值兜底。
        # 原取 rows[0][3]（最新行的策略级配置资金）与账户级净值错配 -> total_pnl 虚增。
        cur = conn.execute("SELECT total_value FROM account_snapshot ORDER BY ts ASC LIMIT 1")
        first = cur.fetchone()
    curve = [{"ts": str(r[0])[:19], "value": float(r[1]) if r[1] else 0, "daily_pnl": float(r[2]) if r[2] else 0} for r in reversed(rows)]
    today_pnl = curve[-1]["daily_pnl"] if curve else 0
    initial = float(first[0]) if first and first[0] else (float(rows[-1][3]) if rows and rows[-1][3] is not None else 1000000)
    total_pnl = (curve[-1]["value"] - initial) if curve else 0
    return {"curve": curve, "today_pnl": today_pnl, "total_pnl": total_pnl, "total_pnl_pct": round(total_pnl/initial*100, 2)}


@router.get("/api/orders")
def get_orders(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """订单记录（order_log 最近 100，#6）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM order_log LIMIT 1")
        except Exception:
            logger.warning("get_orders: order_log 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT ts, strategy_id, symbol, action, volume, price, status FROM order_log ORDER BY ts DESC LIMIT 100")
        rows = cur.fetchall()
    return {"orders": [{"ts": str(r[0])[:19], "strategy_id": r[1], "symbol": r[2], "action": r[3], "volume": r[4], "price": float(r[5]) if r[5] else 0, "status": r[6]} for r in rows], "total": len(rows)}


@router.get("/api/account")
def list_accounts(payload: dict = Depends(require_perm("account_keys"))):
    """列券商/交易所账户（密钥不返回明文）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM accounts LIMIT 1")
        except Exception:
            logger.warning("list_accounts: accounts 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT id, name, exchange, api_key_hint, enabled, created_at FROM accounts ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "exchange": r[2], "api_key_hint": r[3],
             "enabled": r[4], "created_at": str(r[5])} for r in rows]


@router.post("/api/account")
def create_account(req: dict = Body(...), payload: dict = Depends(require_perm("account_keys"))):
    """P4-5 创建账户。"""
    with get_conn() as conn:
        k = (req.get("name", ""), req.get("exchange", ""), req.get("api_key_hint", ""), req.get("enabled", True))
        cur = conn.execute("INSERT INTO accounts (name, exchange, api_key_hint, enabled) VALUES (%s,%s,%s,%s) RETURNING id", k)
        conn.commit()
        return {"id": cur.fetchone()[0]}


@router.get("/api/account/{aid}")
def get_account(aid: int, payload: dict = Depends(require_perm("account_keys"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, exchange, api_key_hint, enabled, created_at FROM accounts WHERE id=%s", (aid,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "账户不存在")
    return {"id": row[0], "name": row[1], "exchange": row[2], "api_key_hint": row[3], "enabled": row[4]}


@router.post("/api/account/{aid}")
def update_account(aid: int, req: dict = Body(...), payload: dict = Depends(require_perm("account_keys"))):
    """P4-5 更新账户。"""
    with get_conn() as conn:
        for k in ("name", "exchange", "api_key_hint", "enabled"):
            if k in req:
                conn.execute(f"UPDATE accounts SET {k}=%s WHERE id=%s", (req[k], aid))
        conn.commit()
    return {"ok": True}


@router.delete("/api/account/{aid}")
def delete_account(aid: int, payload: dict = Depends(require_perm("account_keys"))):
    """P4-5 删除账户。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE id=%s", (aid,))
        conn.commit()
    return {"ok": True}


@router.get("/api/dashboard")
def get_dashboard(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """Dashboard 量化指标（account_snapshot + 回测绩效，#10）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM account_snapshot LIMIT 1")
        except Exception:
            logger.warning("get_dashboard: account_snapshot 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute("SELECT total_value, daily_pnl, initial_capital FROM account_snapshot ORDER BY ts DESC LIMIT 1")
        snap = cur.fetchone()
        # #10 口径修正（2026-08-22）：initial=账户首条快照净值（数据基线），列值兜底（同 /api/position）
        cur = conn.execute("SELECT total_value FROM account_snapshot ORDER BY ts ASC LIMIT 1")
        first = cur.fetchone()
        cur = conn.execute("SELECT COUNT(*) FROM backtest_runs WHERE status='done'")
        bt = cur.fetchone()
    total_value = float(snap[0]) if snap else 0
    initial = float(first[0]) if first and first[0] else (float(snap[2]) if snap and snap[2] is not None else 1000000)
    total_pnl = (total_value - initial) if snap else 0
    return {"total_value": total_value, "total_pnl": total_pnl,
            "total_pnl_pct": round(total_pnl / initial * 100, 2) if (snap and initial) else 0,
            "daily_pnl": float(snap[1]) if snap else 0, "backtest_count": bt[0]}