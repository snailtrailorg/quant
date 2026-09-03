"""回测 + 标的池 + Broker 用量 · 路由"""

from __future__ import annotations
import json
import asyncio
import logging
import os
import redis
from fastapi import APIRouter, Depends, Request, Body, WebSocket, WebSocketDisconnect
from ..auth import require_role, require_perm, audit_log
from ..errors import ApiError
from ..models import (LoginReq, UserCreate, StrategyConfig, InviteReq, RegisterReq, ForgotReq, ResetReq, ChangePwdReq, LogAnalyzeReq, ChatReq, LLMModelReq, IMBotCreateReq, IMBotUpdateReq, IMBotUserReq, LlmBudgetReq, DataSourceReq, ChannelReq, BrokerReq, RiskRuleReq, PoolReq, StrategyAccountReq)
from src.data_platform.db import get_conn

logger = logging.getLogger("web_api")

_redis_pool = redis.ConnectionPool.from_url(
    os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
    decode_responses=True,
)

router = APIRouter(tags=["backtest"])


@router.get("/api/pool")
def list_pools(payload: dict = Depends(require_perm("read"))):
    """标的池列表（含 symbols，#22）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM pools LIMIT 1")
        except Exception:
            logger.warning("list_pools: pools 表不存在（需运行 alembic upgrade head）")
        try:
            conn.execute("SELECT 1 FROM pool_symbols LIMIT 1")
        except Exception:
            logger.warning("list_pools: pool_symbols 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute(
            "SELECT p.id, p.name, p.category, p.description, ps.symbol, p.minute_history_start "
            "FROM pools p LEFT JOIN pool_symbols ps ON ps.pool_id=p.id ORDER BY p.id")
        rows = cur.fetchall()
    pools = {}
    for pid, pname, pcat, pdesc, sym, mhs in rows:
        if pid not in pools:
            pools[pid] = {"id": pid, "name": pname, "category": pcat, "description": pdesc,
                          "symbols": [], "minute_history_start": str(mhs) if mhs else None}
        if sym:
            pools[pid]["symbols"].append(sym)
    return list(pools.values())


@router.post("/api/pool")
def create_pool(req: PoolReq, payload: dict = Depends(require_perm("strategy_control"))):
    """新建/更新标的池（#22）。"""
    symbols = [s.strip() for s in (req.symbolsStr or "").split("\n") if s.strip()]
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM pools LIMIT 1")
        except Exception:
            logger.warning("create_pool: pools 表不存在（需运行 alembic upgrade head）")
        try:
            conn.execute("SELECT 1 FROM pool_symbols LIMIT 1")
        except Exception:
            logger.warning("create_pool: pool_symbols 表不存在（需运行 alembic upgrade head）")
        mhs = req.minute_history_start or None
        conn.execute(
            "INSERT INTO pools (id, name, category, description, minute_history_start) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, category=EXCLUDED.category, "
            "description=EXCLUDED.description, "
            "minute_history_start=COALESCE(EXCLUDED.minute_history_start, pools.minute_history_start)",
            (req.id, req.name, req.category, req.description, mhs))
        conn.execute("DELETE FROM pool_symbols WHERE pool_id=%s", (req.id,))
        for sym in symbols:
            conn.execute("INSERT INTO pool_symbols (pool_id, symbol) VALUES (%s,%s) ON CONFLICT DO NOTHING", (req.id, sym))
        conn.commit()
    audit_log(payload["username"], "create_pool", req.id)
    return {"ok": True, "id": req.id, "count": len(symbols)}


@router.post("/api/pool/{pid}/symbol")
def add_pool_symbol_api(pid: str, body: dict = Body(...),
                        payload: dict = Depends(require_perm("strategy_control"))):
    """单标的入池（链条打磨：替代全量覆盖式 POST /api/pool——修并发覆盖竞态）。

    body: {symbol}——接受 vt 格式（600000.SHSE）或 Tushare 格式（600000.SH），归一到 vt。
    """
    from src.data_platform.schema import to_vt_symbol, vt_to_ts
    raw = (body.get("symbol") or "").strip()
    if not raw:
        raise ApiError(400, "MISSING_FIELDS", "symbol 必填")
    if "." not in raw:
        raise ApiError(400, "SYMBOL_INVALID", f"symbol 需带交易所后缀（如 600000.SHSE）: {raw}")
    vt = to_vt_symbol(raw)
    ts = vt_to_ts(vt)   # 校验可转换（防垃圾格式入池后同步空转——S-F1）
    with get_conn() as conn:
        cur = conn.execute("SELECT id, category FROM pools WHERE id=%s", (pid,))
        row = cur.fetchone()
        if not row:
            raise ApiError(404, "POOL_NOT_FOUND", f"池 {pid} 不存在")
        conn.execute(
            "INSERT INTO pool_symbols (pool_id, symbol) VALUES (%s, %s) "
            "ON CONFLICT (pool_id, symbol) DO NOTHING", (pid, vt))
        conn.commit()
    # 二档深度数据回补（U 审项 9）：增量游标只认窗口，新标的的历史靠这一投——
    # 异步不阻塞响应；非 astock 池不投（pool_data 只拉 astock）
    if row[1] == "astock":
        from src.scheduler.tasks import pool_data_sync_task
        pool_data_sync_task.delay(symbols=[ts])
    audit_log(payload["username"], "pool_add_symbol", pid, vt)
    return {"status": "added", "symbol": vt, "ts_code": ts, "backfill": row[1] == "astock"}


@router.delete("/api/pool/{pid}/symbol/{sym}")
def del_pool_symbol_api(pid: str, sym: str,
                        payload: dict = Depends(require_perm("strategy_control"))):
    """单标的移出池。"""
    from src.data_platform.schema import to_vt_symbol
    vt = to_vt_symbol(sym)
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM pool_symbols WHERE pool_id=%s AND symbol=%s RETURNING id",
                           (pid, vt))
        deleted = cur.fetchone()
        conn.commit()
    if not deleted:
        raise ApiError(404, "POOL_SYMBOL_NOT_FOUND", f"{vt} 不在池 {pid}")
    audit_log(payload["username"], "pool_del_symbol", pid, vt)
    return {"status": "removed", "symbol": vt}


@router.get("/api/pool/{pid}/minute-status")
def pool_minute_status_api(pid: str,
                           payload: dict = Depends(require_perm("read"))):
    """池分钟数据覆盖状态（每标的 bar_1min 最后 ts——首轮回补可能 11.5h，进度可见是必须项）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT ps.symbol, COALESCE(b.last_ts::text, '') FROM pool_symbols ps "
            "LEFT JOIN (SELECT symbol, MAX(ts) AS last_ts FROM bar_1min GROUP BY symbol) b "
            "ON b.symbol = ps.symbol WHERE ps.pool_id=%s ORDER BY ps.symbol", (pid,))
        rows = cur.fetchall()
    return {"pool_id": pid, "symbols": [
        {"symbol": r[0], "last_ts": r[1][:19] if r[1] else None, "covered": bool(r[1])}
        for r in rows]}


@router.delete("/api/pool/{pid}")
def delete_pool(pid: str, payload: dict = Depends(require_perm("strategy_control"))):
    """删除标的池（CASCADE 删 symbols，#22）。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM pools WHERE id=%s", (pid,))
        conn.commit()
    return {"ok": True}


@router.delete("/api/backtest/{run_id}")
def delete_backtest(run_id: int, payload: dict = Depends(require_perm("strategy_control"))):
    """删除回测 run（backtest_symbols 随外键 ondelete=CASCADE 级联删）。

    造数脚本（seed-backtest/verify-gate）收尾清理测试 run，避免污染回测列表/成绩单。
    幂等：run 不存在返回 ok。盲审 P2：状态守卫（非终态 409 防 FK 违例）+ 审计。
    """
    with get_conn() as conn:
        cur = conn.execute("SELECT status FROM backtest_runs WHERE id=%s", (run_id,))
        row = cur.fetchone()
        if not row:
            conn.commit()
            return {"ok": True}   # 幂等：不存在返回 ok
        if row[0] in ("pending", "running"):
            conn.commit()
            raise ApiError(409, "RUN_ACTIVE", f"run {run_id} 仍在 {row[0]}，仅终态可删")
        conn.execute("DELETE FROM backtest_runs WHERE id=%s", (run_id,))
        conn.commit()
    audit_log(payload["username"], "backtest_delete", f"run {run_id}")
    return {"ok": True}


@router.post("/api/backtest")
def create_backtest_api(body: dict = Body(...),
                        payload: dict = Depends(require_perm("strategy_control"))):
    """启动回测 run：写 backtest_runs + Celery backtest_run_task。

    支持 symbol_params：per-symbol 参数覆盖。
    """
    strategy_id = body.get("strategy_config_id")
    symbols = body.get("symbols", [])
    pool_id = body.get("pool_id")
    if pool_id:
        with get_conn() as conn:
            cur = conn.execute("SELECT symbol FROM pool_symbols WHERE pool_id=%s", (pool_id,))
            symbols = [r[0] for r in cur.fetchall()]
    if not symbols or not strategy_id:
        raise ApiError(400, "MISSING_FIELDS", "需 strategy_config_id + symbols/pool_id")
    params = body.get("params", {})
    symbol_params = body.get("symbol_params", {})  # per-symbol 参数覆盖
    mode = body.get("mode", "single")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO backtest_runs (strategy_config_id, symbols, params, symbol_params, mode, status) "
            "VALUES (%s,%s,%s,%s,%s,'pending') RETURNING id",
            (strategy_id, json.dumps(symbols), json.dumps(params),
             json.dumps(symbol_params), mode))
        run_id = cur.fetchone()[0]
        conn.commit()
    from src.scheduler.tasks import backtest_run_task
    task = backtest_run_task.delay(run_id)
    audit_log(payload["username"], "backtest_create", f"run {run_id}")
    return {"run_id": run_id, "task_id": task.id}


@router.get("/api/broker-usage")
def broker_usage(payload: dict = Depends(require_perm("read"))):
    """通道调用量监控（#37，broker_usage 表聚合）。"""
    with get_conn() as conn:
        try:
            conn.execute("SELECT 1 FROM broker_usage LIMIT 1")
        except Exception:
            logger.warning("broker_usage: broker_usage 表不存在（需运行 alembic upgrade head）")
        cur = conn.execute(
            "SELECT provider, COUNT(*), COALESCE(AVG(latency_ms),0), "
            "CASE WHEN COUNT(*)>0 THEN round(SUM(CASE WHEN success THEN 1 ELSE 0 END)*100.0/COUNT(*),1) ELSE 0 END "
            "FROM broker_usage WHERE ts::date=current_date GROUP BY provider ORDER BY COUNT(*) DESC")
        today = [{"provider": r[0], "calls": r[1], "avg_latency_ms": int(r[2]), "success_rate": float(r[3])} for r in cur.fetchall()]
        cur = conn.execute(
            "SELECT ts::date AS d, COUNT(*), COALESCE(AVG(latency_ms),0) FROM broker_usage "
            "WHERE ts >= current_date - interval '7 days' GROUP BY d ORDER BY d")
        trend = [{"date": str(r[0]), "calls": r[1], "avg_latency_ms": int(r[2])} for r in cur.fetchall()]
    return {"today": today, "trend": trend}


def _safe_json(v, fallback):
    """H11（01 P0#4）：中断残行/引擎写坏的 JSON 不再炸整页 500——单行降级+留痕。"""
    if not v:
        return fallback
    try:
        return json.loads(v)
    except Exception:
        logger.warning("backtest JSON 字段解析失败（降级 %r）: %r", type(fallback).__name__, str(v)[:120])
        return fallback


@router.get("/api/backtest")
def list_backtest_api(payload: dict = Depends(require_perm("read"))):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT b.id, b.strategy_config_id, b.symbols, b.mode, b.status, b.created_at, b.finished_at, b.summary_metrics, "
            "b.task_id "
            "FROM backtest_runs b ORDER BY b.id DESC LIMIT 100")
        rows = cur.fetchall()
    return [{"id": r[0], "strategy_config_id": r[1], "symbols": _safe_json(r[2], []),
             "task_id": r[8],   # H11 根修(backtest_symbols 无 task_id 列——原子查询 UndefinedColumn=500 真因,改 runs 级)
             "mode": r[3], "status": r[4], "created_at": str(r[5]) if r[5] else None,
             "finished_at": str(r[6]) if r[6] else None,
             "summary": _safe_json(r[7], {})} for r in rows]


@router.get("/api/backtest/{run_id}")
def get_backtest_api(run_id: int,
                     payload: dict = Depends(require_perm("read"))):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, strategy_config_id, symbols, params, mode, status, summary_metrics, created_at, finished_at, task_id "
            "FROM backtest_runs WHERE id=%s", (run_id,))
        r = cur.fetchone()
        if not r:
            raise ApiError(404, "BACKTEST_NOT_FOUND", "run 不存在")
        # H11 同款修复（详情端点漏修——backtest_symbols 无 task_id 列，原子查询 UndefinedColumn=500）：
        # task_id 取 runs 级
        cur = conn.execute(
            "SELECT symbol, status, result FROM backtest_symbols WHERE run_id=%s ORDER BY symbol",
            (run_id,))
        syms = cur.fetchall()
    # 链条打磨#16（2026-08-19）：补前端实际读取的形状——顶层绩效四卡（此前恒 '-'）+
    # symbols 改对象数组（此前字符串数组致状态列空白）+ 顶层 task_id（终止按钮 #17）
    _mk = [_s for _s in syms if _s[1] == "done"]
    _agg = {}
    for k in ("total_return_pct", "win_rate", "max_drawdown_pct", "sharpe_ratio", "total_trades",
              "volatility", "sortino_ratio", "alpha", "beta", "information_ratio",
              "benchmark_return", "benchmark_volatility"):
        vals = [float((json.loads(_s[2]) or {}).get(k) or 0) for _s in _mk if _s[2]]
        _agg[k] = round(sum(vals) / len(vals), 3) if vals else None
    _run_task = r[9]   # H11：runs 级 task_id
    # wd-20 §1.2 验证门派生字段（单点）：params 区间优先，回落 created_at→finished_at
    import datetime as _dt

    def _d(x):
        try:
            return _dt.date.fromisoformat(str(x)[:10])
        except Exception:
            return None

    _p = json.loads(r[3]) or {}
    _s0, _e0 = _d(_p.get("start")), _d(_p.get("end"))
    if _s0 and _e0:
        span_days = (_e0 - _s0).days + 1
    else:
        # 盲审B-P3：回退改 done 符号 result.start/end_date 聚合（引擎每符号都写）——
        # 度量回测窗口而非执行时长（分钟级 run 用 created→finished 会恒 1 天被 90 门误拦）
        _ss = [_d((json.loads(_x[2]) or {}).get("start_date")) for _x in syms if _x[1] == "done" and _x[2]]
        _ee = [_d((json.loads(_x[2]) or {}).get("end_date")) for _x in syms if _x[1] == "done" and _x[2]]
        _ss = [x for x in _ss if x]; _ee = [x for x in _ee if x]
        if _ss and _ee:
            span_days = (max(_ee) - min(_ss)).days + 1
        else:
            span_days = 0
    return {"id": r[0], "strategy_config_id": r[1],
            "span_days": span_days,
            "symbols": [{"symbol": _s[0], "status": _s[1],
                         "result": json.loads(_s[2]) if _s[2] else {}} for _s in syms],
            "symbols_list": json.loads(r[2]),
            "params": json.loads(r[3]), "mode": r[4], "status": r[5],
            "summary": json.loads(r[6]) if r[6] else {},
            "task_id": _run_task,
            "total_return_pct": _agg.get("total_return_pct"),
            "win_rate": _agg.get("win_rate"),
            "max_drawdown_pct": _agg.get("max_drawdown_pct"),
            "sharpe_ratio": _agg.get("sharpe_ratio"),
            "volatility": _agg.get("volatility"),
            "sortino_ratio": _agg.get("sortino_ratio"),
            "alpha": _agg.get("alpha"),
            "beta": _agg.get("beta"),
            "information_ratio": _agg.get("information_ratio"),
            "benchmark_return": _agg.get("benchmark_return"),
            "benchmark_volatility": _agg.get("benchmark_volatility"),
            "total_trades": _agg.get("total_trades"),
            "symbols_detail": [{"symbol": _s[0], "status": _s[1], "result": json.loads(_s[2]) if _s[2] else {}}
                              for _s in syms]}


@router.get("/api/backtest/{run_id}/summary")
def backtest_summary(run_id: int, payload: dict = Depends(require_perm("read"))):
    """回测组汇总：标的绩效平均+排名（#22）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT symbol, result FROM backtest_symbols WHERE run_id=%s AND status='done'", (run_id,))
        rows = cur.fetchall()
    metrics_keys = ["total_return_pct", "win_rate", "max_drawdown_pct", "sharpe_ratio", "total_trades",
                    "volatility", "sortino_ratio", "alpha", "beta", "information_ratio",
                    "benchmark_return", "benchmark_volatility"]
    results = []
    for sym, result_json in rows:
        r = json.loads(result_json) if result_json else {}
        results.append({"symbol": sym, **{k: r.get(k, 0) for k in metrics_keys}})
    ranked = sorted(results, key=lambda x: x.get("total_return_pct", 0), reverse=True)
    avg = {k: round(sum(r[k] for r in results) / len(results), 3) for k in metrics_keys} if results else {}
    return {"run_id": run_id, "count": len(results), "avg": avg, "ranked": ranked}


@router.get("/api/backtest/{run_id}/{symbol}/stream")
def backtest_stream_api(run_id: int, symbol: str,
                        payload: dict = Depends(require_perm("read"))):
    """SSE 单标的实时（轮询 Valkey backtest:run:{run_id}:{symbol}）。"""
    from fastapi.responses import StreamingResponse
    r = redis.Redis(connection_pool=_redis_pool)
    key = f"backtest:run:{run_id}:{symbol}"

    async def gen():
        for _ in range(720):  # 最多 6 分钟
            done = r.get(key + ":done")
            if done:
                yield f"data: {done}\n\n"
                break
            err = r.get(key + ":error")
            if err:
                yield f"data: {json.dumps({'error': err})}\n\n"
                break
            frame = r.get(key)
            if frame:
                yield f"data: {frame}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream")