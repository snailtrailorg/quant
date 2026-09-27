"""批 62b（M7）：质量对账路由——shadow diff 列表/白名单统计/SM 差集/血缘链/手动触发+验证回填。

报表消费方=前端 /dataops 第四 tab「对账」（DataIntegrity 同族）；60s 聚合缓存先例（risk.py）。
写操作（触发/回填）挂 require_perm("system_config")。
"""
from fastapi import APIRouter, Query

from src.web_api.auth import require_perm
from src.web_api.errors import ApiError

router = APIRouter(prefix="/api/quality", tags=["quality"])

_AGG_CACHE: dict = {"ts": 0.0, "stats": None}   # 白名单统计 60s 聚合（risk.py:327 先例）


@router.get("/shadow-diff")
async def shadow_diff_list(kind: str = Query(default="bar_daily"), days: int = Query(default=30, le=90),
                           real_only: bool = Query(default=False), limit: int = Query(default=200, le=1000),
                           _u=require_perm("system_config")):
    """diff 行列表（报表主表；real_only=只看真差异——白名单命中行可筛除）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        sql = ("SELECT id, kind, symbol, trade_date, field, main_val, backup_val, whitelist_hit, created_at "
               "FROM shadow_diff WHERE kind=%s AND created_at >= now() - interval %s day")
        args: list = [kind, days]
        if real_only:
            sql += " AND whitelist_hit IS NULL"
        sql += " ORDER BY created_at DESC LIMIT %s"
        args.append(limit)
        rows = [dict(zip(("id", "kind", "symbol", "trade_date", "field", "main_val",
                          "backup_val", "whitelist_hit", "created_at"), r))
                for r in conn.execute(sql, args).fetchall()]
    return {"rows": rows}


@router.get("/whitelist-stats")
async def whitelist_stats(days: int = Query(default=30), _u=require_perm("system_config")):
    """白名单命中率双向统计+上次验证（治理③——A-P1-7）。"""
    import time
    now = time.time()
    if _AGG_CACHE["stats"] is not None and now - _AGG_CACHE["ts"] < 60:
        return _AGG_CACHE["stats"]
    from src.data_platform.quality import whitelist_stats as _ws
    stats = {"entries": _ws(days)}
    _AGG_CACHE.update(ts=now, stats=stats)
    return stats


@router.post("/whitelist-verify/{entry_id}")
async def whitelist_verify(entry_id: str, result: str = Query(default=""), _u=require_perm("system_config")):
    """验证义务回填（A-P1-7：system_config whitelist_verify:{id}——报表列读它）。"""
    from datetime import date
    from src.data_platform.db import get_conn
    from src.data_platform.quality import load_whitelist
    if entry_id not in {e["id"] for e in load_whitelist()}:
        raise ApiError(404, "NOT_FOUND", "白名单条目不存在")
    with get_conn() as conn:
        conn.execute("INSERT INTO system_config (key, value) VALUES (%s,%s) "
                     "ON CONFLICT (key) DO UPDATE SET value=excluded.value",
                     (f"whitelist_verify:{entry_id}", f"{date.today().isoformat()} {result}".strip()))
        conn.commit()
    return {"ok": True, "entry": entry_id}


@router.get("/sm-reconcile")
async def sm_reconcile(_u=require_perm("system_config")):
    """SM 差集报告（62c——范围钉死 stock×L；ETF/转债范围外不计）。"""
    from src.data_platform.quality import sm_reconcile as _sr
    return _sr()


@router.get("/lineage/{signal_id}")
async def lineage(signal_id: int, _u=require_perm("system_config")):
    """审计链贯通（验收②）：signal→order_log.bar_fingerprint→血缘三列一条链（A03 §15.4）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT s.id, s.ts, s.strategy_id, s.symbol, s.action, s.source, s.fetched_at, s.dataset_version, "
            "o.bar_fingerprint FROM signal_log s "
            "LEFT JOIN order_log o ON o.signal_id = s.id "
            "WHERE s.id = %s ORDER BY o.id DESC LIMIT 1", (signal_id,))
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "NOT_FOUND", "signal 不存在")
    return {"signal": dict(zip(("id", "ts", "strategy_id", "symbol", "action",
                                "source", "fetched_at", "dataset_version"), r[:8])),
            "bar_fingerprint": r[8]}


@router.post("/shadow-check")
async def trigger_shadow_check(kind: str = Query(default="bar_daily"), _u=require_perm("system_config")):
    """手动触发采样对账（验收③——staging 实跑入口；与 beat 同一函数）。"""
    from src.data_platform.quality import run_shadow_check
    return run_shadow_check(kind)
