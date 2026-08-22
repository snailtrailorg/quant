"""Web 后端 · 数据同步路由（/api/sync/* + /api/data-source-usage）。"""

from fastapi import APIRouter, Depends, Request, Body, WebSocket, WebSocketDisconnect
from ..auth import require_role, require_perm, audit_log
from ..errors import ApiError
from ..models import (LoginReq, UserCreate, StrategyConfig, InviteReq, RegisterReq, ForgotReq, ResetReq, ChangePwdReq, LogAnalyzeReq, ChatReq, LLMModelReq, IMBotCreateReq, IMBotUpdateReq, IMBotUserReq, LlmBudgetReq, DataSourceReq, ChannelReq, BrokerReq, RiskRuleReq, PoolReq, StrategyAccountReq)
from src.data_platform.db import get_conn
import logging
import json
import os
import redis

logger = logging.getLogger("web_api")

router = APIRouter(tags=["sync"])

# Redis 连接池（各端点复用，避免每次请求新建连接）
_redis_pool = redis.ConnectionPool.from_url(
    os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
    decode_responses=True,
)


@router.get("/api/sync/config")
def list_sync_config(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, tushare_api, pg_table, data_type, sync_mode, schedule, trade_day_filter, enabled, last_sync_date, last_sync_ts, last_sync_count, last_status, description FROM sync_config ORDER BY id")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "tushare_api": r[2], "pg_table": r[3], "data_type": r[4], "sync_mode": r[5], "schedule": r[6], "trade_day_filter": r[7], "enabled": r[8], "last_sync_date": r[9], "last_sync_ts": str(r[10]) if r[10] else None, "last_sync_count": r[11], "last_status": r[12], "description": r[13]} for r in rows]


@router.post("/api/sync/config/{sid}")
def update_sync_config_api(sid: str, body: dict, payload: dict = Depends(require_perm("data_sync"))):
    with get_conn() as conn:
        conn.execute("UPDATE sync_config SET schedule=%s, enabled=%s, trade_day_filter=%s WHERE id=%s",
            (body.get("schedule"), body.get("enabled"), body.get("trade_day_filter"), sid))
        conn.commit()
    audit_log(payload["username"], "update_sync_config", sid)
    return {"ok": True}


@router.post("/api/sync/trigger/{sid}")
def trigger_sync_api(sid: str, backfill_from: str | None = None, payload: dict = Depends(require_perm("data_sync"))):
    """异步触发类型级同步：提交 Celery 后台任务，立即返回 task_id（不阻塞 HTTP）。"""
    from src.scheduler.tasks import sync_via_celery
    task = sync_via_celery.delay(sid, backfill_from)
    audit_log(payload["username"], "trigger_sync", sid)
    return {"status": "submitted", "task_id": task.id}


@router.post("/api/sync/pool-data/trigger")
def trigger_pool_data_api(full: bool = False, payload: dict = Depends(require_perm("data_sync"))):
    """手动触发池内深度数据同步（beat 300s 也自动跑）。

    full=true 全量校准（无视游标窗口）——定期跑防上游改历史漏数据。
    """
    from src.scheduler.tasks import pool_data_sync_task
    task = pool_data_sync_task.delay(full=full)
    audit_log(payload["username"], "trigger_pool_data", "full" if full else "")
    return {"status": "submitted", "task_id": task.id, "full": full}


@router.get("/api/sync/pool-data/progress")
def pool_data_progress_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """池深度数据同步进度——读 sync_log 最新一轮。

    2026-08-20 修正：原读 Valkey sync:pool:minute（池分钟同步的键，pool_data 从不写）→ 恒 idle。
    pool_data 结果落 sync_log（sync_id='pool_data'），rows_pulled 列存标的数。
    """
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT ts, rows_pulled, rows_saved, status, error FROM sync_log "
            "WHERE sync_id='pool_data' ORDER BY ts DESC LIMIT 1")
        r = cur.fetchone()
    if not r:
        return {"status": "idle", "reason": "无同步记录"}
    return {"status": r[3], "symbols": r[1], "saved": r[2],
            "error": r[4] or "", "ts": str(r[0]) if r[0] else None}


@router.post("/api/sync/pool-minute/trigger")
def trigger_pool_minute_api(payload: dict = Depends(require_perm("data_sync"))):
    """手动触发池分钟同步（beat 300s 也会自动跑；手动用于首建池后立即拉取）。"""
    from src.scheduler.tasks import pool_minute_sync_task
    task = pool_minute_sync_task.delay()
    audit_log(payload["username"], "trigger_pool_minute", "")
    return {"status": "submitted", "task_id": task.id}


@router.get("/api/sync/pool-minute/progress")
def pool_minute_progress_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """池分钟同步进度（Valkey sync:pool:minute hash）。"""
    r = redis.Redis(connection_pool=_redis_pool)
    data = r.hgetall("sync:pool:minute")
    out = {}
    for k, v in data.items():
        ks = k.decode() if isinstance(k, bytes) else k
        vs = v.decode() if isinstance(v, bytes) else v
        out[ks] = vs
    return out or {"status": "idle"}


@router.post("/api/sync/adj-factor-backfill")
def adj_factor_backfill_api(start_date: str | None = None, end_date: str | None = None,
                            payload: dict = Depends(require_perm("data_sync"))):
    """复权因子回填（A/B-F1：bar_1D 历史全 NULL）。Tushare 积分到账后手动触发一次即可。

    降级安全：积分未到账时任务返回 degraded（不抛异常不崩），到账后重新触发续填。
    """
    from src.scheduler.tasks import adj_factor_backfill_task
    task = adj_factor_backfill_task.delay(start_date, end_date)
    audit_log(payload["username"], "adj_factor_backfill", f"{start_date or '全历史'}~{end_date or '今'}")
    return {"status": "submitted", "task_id": task.id, "progress": "sync:adj-factor"}


@router.get("/api/sync/adj-factor-backfill/progress")
def adj_factor_backfill_progress_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """复权因子回填进度（Valkey sync:adj-factor hash；status=degraded 表示积分未到账降级）。"""
    r = redis.Redis(connection_pool=_redis_pool)
    data = r.hgetall("sync:adj-factor")
    out = {}
    for k, v in data.items():
        ks = k.decode() if isinstance(k, bytes) else k
        vs = v.decode() if isinstance(v, bytes) else v
        out[ks] = int(vs) if ks in ("done", "total", "pct") and vs.lstrip("-").isdigit() else vs
    return out or {"status": "idle"}


@router.get("/api/sync/trigger/{sid}/progress")
def trigger_progress_api(sid: str, task_id: str | None = None,
                         payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """查类型级同步进度（Valkey sync:type:{sid}，无则 Celery AsyncResult 兜底）。"""
    r = redis.Redis(connection_pool=_redis_pool)
    data = r.hgetall(f"sync:type:{sid}")
    _INT_FIELDS = {"done", "total", "pct", "rows_pulled", "rows_saved",
                   "expected_days", "actual_days", "failed_dates_count"}
    if data:
        out = {}
        for k, v in data.items():
            ks = k.decode() if isinstance(k, bytes) else k
            vs = v.decode() if isinstance(v, bytes) else v
            out[ks] = int(vs) if ks in _INT_FIELDS and vs.lstrip("-").isdigit() else vs
        return out
    # 兜底：查 Celery AsyncResult（hash 过期/worker 重启时）
    if task_id:
        from src.scheduler.app import app as celery_app
        res = celery_app.AsyncResult(task_id)
        if res.state == "SUCCESS":
            d = res.result or {}
            return {"status": d.get("status", "success"),
                    "rows_pulled": d.get("rows_pulled", 0),
                    "rows_saved": d.get("rows_saved", 0),
                    "expected_days": d.get("expected_days") or 0,
                    "actual_days": d.get("actual_days") or 0,
                    "failed_dates_count": len(d.get("failed_dates") or [])}
        if res.state in ("PENDING", "STARTED", "RETRY"):
            return {"status": "running"}
        if res.state == "FAILURE":
            return {"status": "error", "error": str(res.result)[:120]}
    return {"status": "idle"}


# --- per-symbol 同步端点（2026-08-04 端点误删恢复，基于 engine 现有函数重建） ---

@router.get("/api/sync/symbols/{sid}")
def list_symbols_api(sid: str, q: str = "", page: int = 1, size: int = 9999,
                     payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    from src.data_sync.engine import list_symbols
    return list_symbols(sid, q=q, page=page, size=size)


@router.post("/api/sync/symbol/{sid}/{ts_code}")
def sync_symbol_api(sid: str, ts_code: str, body: dict = Body(default={}),
                    payload: dict = Depends(require_perm("data_sync"))):
    from src.data_sync.engine import sync_symbol
    mode = body.get("mode", "auto") if body else "auto"
    result = sync_symbol(sid, ts_code, mode=mode)
    audit_log(payload["username"], "sync_symbol", f"{sid}:{ts_code}")
    return result


@router.post("/api/sync/symbol/{sid}/{ts_code}/backfill")
def backfill_symbol_api(sid: str, ts_code: str, body: dict = Body(...),
                        payload: dict = Depends(require_perm("data_sync"))):
    from src.data_sync.engine import backfill_symbol
    result = backfill_symbol(sid, ts_code, body.get("start", ""), body.get("end", ""))
    audit_log(payload["username"], "backfill_symbol", f"{sid}:{ts_code}:{body.get('start')}~{body.get('end')}")
    return result


@router.delete("/api/sync/symbol/{sid}/{ts_code}")
def delete_symbol_api(sid: str, ts_code: str,
                      payload: dict = Depends(require_perm("data_sync"))):
    from src.data_sync.engine import delete_symbol
    result = delete_symbol(sid, ts_code)
    audit_log(payload["username"], "delete_symbol", f"{sid}:{ts_code}")
    return result


@router.post("/api/sync/all/{sid}")
def sync_all_api(sid: str, payload: dict = Depends(require_perm("data_sync"))):
    """提交全市场全量重建（Celery 后台，返回 task_id）。"""
    from src.scheduler.tasks import sync_all_symbols
    task = sync_all_symbols.delay(sid)
    audit_log(payload["username"], "sync_all", sid)
    return {"task_id": task.id}


@router.get("/api/sync/all/{sid}/progress")
def sync_all_progress_api(sid: str, task_id: str | None = None,
                           payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """查全量重建进度（Valkey sync:progress:{sid}，无则 Celery AsyncResult 兜底）。"""
    r = redis.Redis(connection_pool=_redis_pool)
    data = r.hgetall(f"sync:progress:{sid}")
    _INT_FIELDS = {"done", "total", "pct", "ok", "saved", "failed_count"}
    if data:
        out = {}
        for k, v in data.items():
            ks = k.decode() if isinstance(k, bytes) else k
            vs = v.decode() if isinstance(v, bytes) else v
            out[ks] = int(vs) if ks in _INT_FIELDS and vs.lstrip("-").isdigit() else vs
        return out
    if task_id:
        from src.scheduler.app import app as celery_app
        res = celery_app.AsyncResult(task_id)
        if res.state == "SUCCESS":
            d = res.result or {}
            return {"status": d.get("status", "success"),
                    "ok": d.get("ok", 0), "total": d.get("total", 0),
                    "saved": d.get("saved", 0), "failed_count": d.get("failed_count", 0)}
        if res.state in ("PENDING", "STARTED", "RETRY"):
            return {"status": "running"}
        if res.state == "FAILURE":
            return {"status": "error", "error": str(res.result)[:120]}
    return {"status": "idle"}


@router.delete("/api/sync/data/{sid}")
def delete_sync_data_api(sid: str, payload: dict = Depends(require_perm("data_sync"))):
    with get_conn() as conn:
        cur = conn.execute("SELECT pg_table FROM sync_config WHERE id=%s", (sid,))
        r = cur.fetchone()
    if r and r[0]:
        with get_conn() as conn:
            conn.execute(f'DELETE FROM "{r[0]}"')
            conn.execute("UPDATE sync_config SET last_sync_date=NULL, last_sync_ts=NULL, last_sync_count=0, last_status='idle' WHERE id=%s", (sid,))
            conn.commit()
    audit_log(payload["username"], "delete_sync_data", sid)
    return {"ok": True}


@router.get("/api/sync/log")
def get_sync_logs_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    with get_conn() as conn:
        # 列名对齐写入侧（engine._log）：start_date/end_date/rows_pulled/rows_saved——
        # 原查询写成 start/end（PG 保留字+列不存在）→ 端点自出生即 500，2026-08-18 生产验证顺带发现
        cur = conn.execute(
            "SELECT id, sync_id, mode, start_date, end_date, rows_pulled, rows_saved, "
            "duration_ms, status, ts FROM sync_log ORDER BY ts DESC LIMIT 100")
        rows = cur.fetchall()
    return [{"id": r[0], "sync_id": r[1], "mode": r[2],
             "start": str(r[3]) if r[3] else None, "end": str(r[4]) if r[4] else None,
             "rows_pulled": r[5], "rows_saved": r[6], "duration_ms": r[7],
             "status": r[8], "ts": str(r[9]) if r[9] else None} for r in rows]


# --- 数据源用量监控（A4 #36）---

@router.get("/api/data-source-usage")
def data_source_usage_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """数据源调用量监控：by provider 今日聚合 + 7 天趋势。"""
    with get_conn() as conn:
        try:
            cur = conn.execute("""
                SELECT provider,
                    coalesce(sum(calls), 0) as calls,
                    count(*) as records,
                    sum(case when success then 0 else 1 end) as failures,
                    coalesce(round(avg(latency_ms)), 0) as avg_latency
                FROM data_source_usage
                WHERE ts >= date_trunc('day', now())
                GROUP BY provider ORDER BY calls DESC NULLS LAST
            """)
            today = [{"provider": r[0], "calls": r[1], "records": r[2],
                      "failures": r[3], "avg_latency": r[4]} for r in cur.fetchall()]
            cur = conn.execute("""
                SELECT to_char(date_trunc('day', ts), 'YYYYMMDD') as day,
                       provider, coalesce(sum(calls), 0)
                FROM data_source_usage
                WHERE ts >= now() - interval '7 days'
                GROUP BY day, provider ORDER BY day, provider
            """)
            trend = [{"day": r[0], "provider": r[1], "calls": r[2]} for r in cur.fetchall()]
        except Exception:
            return {"today": [], "trend": []}
    return {"today": today, "trend": trend}