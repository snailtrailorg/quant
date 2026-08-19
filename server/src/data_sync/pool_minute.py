"""池驱动分钟线同步（S+T 审修订版 2026-08-19）。

编排归属 data_sync（S-S1：第 4 个"engine 编排+scheduler 壳"同款模式）；
限速归 DataSource.get_rate_limit（T 审：pull_minute 零改动，调用方循环查表）；
stk_mins 硬限走 Valkey 全局闸门（S-F3：1 次/分钟级别太长，进程内 sleep 会占死 worker）。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta

from src.data_platform import db as _pdb
from .sync_lock import SyncLock

logger = logging.getLogger("data_sync.pool_minute")

# stk_mins Valkey 全局闸门（S-F3）：SET NX EX 有界等待，超限抛 RateLimited
STK_MINS_GATE_KEY = "ds:tushare:rl:stk_mins"
STK_MINS_GATE_TIMEOUT_S = 65


class RateLimited(Exception):
    """数据源限速等待超时。engine 路径→failed_dates；HTTP 路径→ApiError(429)。"""


def _stk_mins_gate(r, timeout_s: int = STK_MINS_GATE_TIMEOUT_S) -> None:
    """stk_mins 全局闸门：限速间隔从 DataSource.get_rate_limit('stk_mins') 取。

    有界等待（≤timeout_s）；超限抛 RateLimited——engine 捕获记 failed 下轮续（幂等）。
    key 不含 token（T 审：秘密禁入 redis-cli 可见层）。
    """
    from src.data_platform.data_source import get_data_source, TushareDataSource
    ds = get_data_source("tushare") or TushareDataSource()
    interval = max(1.0, ds.get_rate_limit("stk_mins"))
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if r.set(STK_MINS_GATE_KEY, "1", nx=True, ex=int(interval)):
            return
        time.sleep(min(2.0, max(0.2, interval / 10)))
    raise RateLimited(f"stk_mins 限速等待超时（{timeout_s}s）——下轮自动续补")


def _pool_symbols_with_gap(pool_id: str, start_date: date, today: date) -> list[tuple[str, str, date]]:
    """查池内 bar_1min 有缺口的标的（本地聚合，零 API）。

    返回 [(vt_symbol, ts_code, should_start), ...]——should_start=max(池起始, 无数据时即池起始)。
    """
    from src.data_platform.schema import vt_to_ts
    with _pdb.get_conn() as conn:
        cur = conn.execute(
            "SELECT ps.symbol FROM pool_symbols ps WHERE ps.pool_id=%s", (pool_id,))
        symbols = [r[0] for r in cur.fetchall()]
        if not symbols:
            return []
        # 本地聚合：各标的最后 ts
        cur = conn.execute(
            "SELECT symbol, MAX(ts) FROM bar_1min WHERE symbol = ANY(%s) GROUP BY symbol",
            (symbols,))
        last_ts = {r[0]: r[1] for r in cur.fetchall()}
    today_str = today.strftime("%Y%m%d")
    gaps = []
    for vt in symbols:
        last = last_ts.get(vt)
        # 缺口判定：无数据 OR 最后 ts < 昨天（今天可能还没收盘/同步）
        yesterday = today - timedelta(days=1)
        if last is None or last.date() < yesterday:
            start = start_date
            if last is not None and last.date() > start_date:
                start = last.date() + timedelta(days=1)   # 有部分数据：从断点续
            gaps.append((vt, vt_to_ts(vt), start))
    return gaps


def sync_pools_minute(timebox_s: int = 280) -> dict:
    """池驱动分钟同步（S 修订：时间盒防 soft_time_limit 杀+beat 重叠）。

    - 扫描 pools WHERE minute_history_start IS NOT NULL AND category='astock'（S-S4）
    - 有缺口标的 → _stk_mins_gate → _fetch_minute_and_save（同模块私有 OK）
    - 时间盒到即收工（下轮续——幂等）
    - SyncLock('pool_minute') 防重叠 + sync_log 可观测（S-S3）
    """
    from .engine import _fetch_minute_and_save
    from src.alert_notify.notify import safe_notify

    lock = SyncLock("pool_minute")
    with lock:
        if not lock.acquired:
            return {"status": "skipped", "reason": "上轮仍在运行"}

        today = date.today()
        with _pdb.get_conn() as conn:
            cur = conn.execute(
                "SELECT id, name, minute_history_start FROM pools "
                "WHERE minute_history_start IS NOT NULL AND category='astock'")
            pools = cur.fetchall()

        if not pools:
            return {"status": "idle", "reason": "无配置分钟历史的 A 股池"}

        # Valkey 进度（首轮可能 11.5h——用户必须能看到）
        import redis as _redis
        import os as _os
        r = _redis.Redis.from_url(
            _os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True, socket_timeout=3)

        deadline = time.time() + timebox_s
        total_synced, total_symbols, errors = 0, 0, []
        for pool_id, pool_name, start_date in pools:
            gaps = _pool_symbols_with_gap(pool_id, start_date, today)
            total_symbols += len(gaps)
            for vt, ts_code, should_start in gaps:
                if time.time() >= deadline:
                    _log_pool(pool_id, len(gaps), total_synced, errors, timeout=True)
                    return {"status": "timebox", "pools": len(pools), "synced": total_synced,
                            "pending": total_symbols - total_synced, "errors": errors[:5]}
                try:
                    _stk_mins_gate(r)
                    end = today.strftime("%Y%m%d")
                    _fetch_minute_and_save(ts_code, "1min",
                                           should_start.strftime("%Y%m%d"), end, overwrite=False)
                    total_synced += 1
                    r.hset("sync:pool:minute", mapping={
                        "status": "running", "current": ts_code, "synced": total_synced,
                        "pending": total_symbols - total_synced, "pool": pool_name,
                    })
                    r.expire("sync:pool:minute", 86400)
                except RateLimited as e:
                    errors.append(f"{ts_code}: {e}")
                    break   # 限速满——本轮到此，下轮续
                except Exception as e:
                    errors.append(f"{ts_code}: {type(e).__name__}: {str(e)[:60]}")
                    logger.warning("池分钟同步 %s 失败: %s", ts_code, e)

        _log_pool(None, total_symbols, total_synced, errors, timeout=False)
        r.hset("sync:pool:minute", mapping={
            "status": "done" if not errors else "partial",
            "synced": total_synced, "pending": 0,
            "errors": "; ".join(errors[:3])[:200],
        })
        r.expire("sync:pool:minute", 86400)
        return {"status": "done" if not errors else "partial",
                "pools": len(pools), "synced": total_synced, "errors": errors[:5]}


def _log_pool(pool_id, total: int, synced: int, errors: list, timeout: bool) -> None:
    """sync_log 可观测（S-S3：池同步不入 sync_log=重蹈'断 11 天才发现'教训）。"""
    try:
        with _pdb.get_conn() as conn:
            conn.execute(
                "INSERT INTO sync_log (sync_id, ts, mode, start, end, pulled, saved, duration_ms, status, error, failed_dates) "
                "VALUES (%s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("pool_minute", "", "", "", 0, synced, 0,
                 "timeout" if timeout else ("done" if not errors else "partial"),
                 "", "; ".join(errors[:3])[:200]))
            conn.commit()
    except Exception:
        pass
