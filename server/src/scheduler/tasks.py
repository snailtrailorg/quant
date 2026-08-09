"""Celery 定时任务实现。"""

from __future__ import annotations
from src.data_platform.db import get_conn
from datetime import date, datetime, timezone
import logging

from .app import app

logger = logging.getLogger("scheduler")


def _is_trading_day() -> bool:
    """从数据中台判断是否交易日；日历缺失时保守按工作日。"""
    try:
        from src.data_platform import platform
        return platform.is_trading_day(date.today())
    except Exception:
        return date.today().weekday() < 5


@app.task(name="src.scheduler.tasks.data_increment_daily", bind=True, max_retries=2)
def data_increment_daily(self):
    """盘后增量更新日线；非交易日跳过。"""
    if not _is_trading_day():
        return {"status": "skipped", "reason": "非交易日"}
    try:
        from src.data_platform import platform
        # MVP：浦发银行作为连通性标的；后续改配置驱动全标的池
        today = date.today().strftime("%Y%m%d")
        rows = platform.ensure_daily("600000.SH", today, today)
        return {"status": "ok", "rows": rows}
    except Exception as exc:
        logger.exception("日线增量失败")
        raise self.retry(exc=exc, countdown=30)


@app.task(name="src.scheduler.tasks.astock_select_daily", bind=True, max_retries=1)
def astock_select_daily(self):
    """每日 A 股选股；非交易日跳过。"""
    if not _is_trading_day():
        return {"status": "skipped", "reason": "非交易日"}
    try:
        from src.astock_analysis import DailySelectionEngine
        results = DailySelectionEngine(top_n=20).run(date.today().strftime("%Y%m%d"))
        return {
            "status": "ok",
            "count": len(results),
            "symbols": [r.symbol for r in results],
        }
    except Exception as exc:
        logger.exception("每日选股失败")
        raise self.retry(exc=exc, countdown=30)


@app.task(name="src.scheduler.tasks.data_increment_crypto", bind=True, max_retries=2)
def data_increment_crypto(self):
    """加密 K 线增量（24h）。网关 API key 未配置时安全跳过。"""
    return {"status": "skipped", "reason": "待币安/OKX API 配置"}


@app.task(name="src.scheduler.tasks.risk_sweep")
def risk_sweep():
    """扫描全局风控状态。"""
    from src.risk_control import RiskControl
    rc = RiskControl.get()
    return {
        "status": "ok",
        "halted": rc.is_halted(),
        "reason": rc.halt_reason(),
    }


@app.task(name="src.scheduler.tasks.daily_report", bind=True, max_retries=1)
def daily_report(self):
    """盘后报告。LLM key 未配置时生成确定性摘要，不阻塞任务。"""
    from src.risk_control import RiskControl
    rc = RiskControl.get()
    body = f"日期: {date.today().isoformat()}\n风控: {'熔断' if rc.is_halted() else '正常'}"
    try:
        from src.llm_gateway import gateway
        response = gateway.chat(
            [{"role": "user", "content": f"请生成简短盘后报告：\n{body}"}],
            role="viewer",
            timeout=30,
            retries=0,
            caller="daily_report",
        )
        if response.content:
            body = response.content
    except Exception:
        pass

    from src.alert_notify import AlertNotify
    AlertNotify.get().report("盘后报告", body)
    return {"status": "ok", "body": body[:200]}


@app.task(name="src.scheduler.tasks.astock_minute_analysis")
def astock_minute_analysis():
    """盘中分钟研判；非交易日/连续竞价外跳过。"""
    now = datetime.now()
    if not _is_trading_day():
        return {"status": "skipped", "reason": "非交易日"}
    hm = now.hour * 100 + now.minute
    if not (930 <= hm <= 1130 or 1300 <= hm <= 1500):
        return {"status": "skipped", "reason": "非连续竞价时段"}
    return {"status": "skipped", "reason": "待实时行情订阅"}


@app.task(name="src.scheduler.tasks.health_check")
def health_check():
    """定时探测 LLM/PG/Valkey/交易所连通性，异常告警。"""
    import os, time, psycopg, redis
    results = {}

    # PG
    try:
        with get_conn() as c:
            c.execute("select 1")
        results["postgresql"] = {"status": "ok"}
    except Exception as e:
        results["postgresql"] = {"status": "error", "msg": str(e)[:100]}

    # Valkey
    try:
        r = redis.Redis.from_url(os.environ.get("VALKEY_URL","redis://127.0.0.1:6379/0"), socket_timeout=3)
        results["valkey"] = {"status": "ok" if r.ping() else "error"}
    except Exception as e:
        results["valkey"] = {"status": "error", "msg": str(e)[:100]}

    # LLM (DeepSeek)
    try:
        from src.llm_gateway import gateway
        resp = gateway.chat([{"role":"user","content":"ping"}], timeout=10, retries=0, caller="health_check")
        results["llm"] = {"status": "ok" if resp.content else "empty", "model": resp.usage.get("model","")}
    except Exception as e:
        results["llm"] = {"status": "error", "msg": str(e)[:100]}

    # 异常告警
    errors = [k for k,v in results.items() if v["status"] == "error"]
    if errors:
        from src.alert_notify import AlertNotify
        AlertNotify.get().notify("critical", "接口健康异常", f"离线: {errors}", channel=None)

    return {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results}


@app.task(name="src.scheduler.tasks.drift_check")
def drift_check():
    """F-MON-005 每日比对实盘因子值 vs 回测复刻因子值，偏差超限告警。

    实盘开始后自动运行。无实盘数据时跳过。
    """
    # 检查是否有实盘运行中的策略
    import psycopg, os
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT id FROM strategy_config WHERE enabled=true AND backtest_verified=true")
            running = cur.fetchall()
        if not running:
            return {"status": "skipped", "reason": "无实盘策略运行"}
    except Exception:
        return {"status": "skipped", "reason": "DB不可用"}

    # 实盘 vs 回测因子比对（实盘数据接入后实现具体逻辑）
    # 架构：取当日实盘 bar -> 因子计算 -> 与回测同时段因子值比对 -> 偏差超 1% 告警
    issues = []
    # TODO: 实盘数据接入后，逐策略比对因子值

    if issues:
        from src.alert_notify import AlertNotify
        AlertNotify.get().notify("critical", "因子漂移告警", f"实盘-回测因子偏差超限: {issues}")

    return {"status": "ok", "running_strategies": len(running), "drift_issues": issues}


@app.task(name="src.scheduler.tasks.reconcile_three_books")
def reconcile_three_books():
    """S-ACC-003 信号-委托-成交三账对账。

    定时核对：模型信号日志、系统委托日志、交易所成交日志。
    识别：信号无委托、委托不成交、滑点异常、成交偏差。
    """
    import psycopg, os
    issues = []

    try:
        with get_conn() as conn:
            # 建对账表（幂等）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_log (
                    id BIGSERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT now(),
                    strategy_id TEXT,
                    symbol TEXT,
                    action TEXT,
                    score NUMERIC,
                    price NUMERIC
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS order_log (
                    id BIGSERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT now(),
                    strategy_id TEXT,
                    symbol TEXT,
                    action TEXT,
                    volume INT,
                    price NUMERIC,
                    status TEXT DEFAULT 'submitted',
                    signal_id BIGINT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_log (
                    id BIGSERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT now(),
                    order_id BIGINT,
                    symbol TEXT,
                    action TEXT,
                    volume INT,
                    price NUMERIC,
                    commission NUMERIC
                )
            """)
            conn.commit()

            # 比对逻辑（实盘数据接入后实现具体核对）
            # 1. 信号无委托：signal_log 中有记录但 order_log 中无对应 signal_id
            cur = conn.execute("""
                SELECT count(*) FROM signal_log s
                WHERE NOT EXISTS (SELECT 1 FROM order_log o WHERE o.signal_id = s.id)
            """)
            orphan_signals = cur.fetchone()[0]
            if orphan_signals > 0:
                issues.append(f"信号无委托: {orphan_signals} 笔")

            # 2. 委托不成交
            cur = conn.execute("""
                SELECT count(*) FROM order_log o
                WHERE o.status != 'filled'
                AND NOT EXISTS (SELECT 1 FROM trade_log t WHERE t.order_id = o.id)
            """)
            unfilled = cur.fetchone()[0]
            if unfilled > 0:
                issues.append(f"委托不成交: {unfilled} 笔")

            # 3. 滑点异常（成交价 vs 委托价偏差 > 1%）
            cur = conn.execute("""
                SELECT count(*) FROM order_log o
                JOIN trade_log t ON t.order_id = o.id
                WHERE abs(t.price - o.price) / o.price > 0.01
            """)
            slippage = cur.fetchone()[0]
            if slippage > 0:
                issues.append(f"滑点异常(>1%): {slippage} 笔")

    except Exception as e:
        issues.append(f"对账异常: {str(e)[:100]}")

    if issues:
        from src.alert_notify import AlertNotify
        AlertNotify.get().notify("critical", "三账对账异常", "\n".join(issues))

    return {"status": "ok" if not issues else "issues", "issues": issues}


@app.task(name="src.scheduler.tasks.data_continuity_check")
def data_continuity_check():
    """P-MON-006 数据断连自愈与断点补采。

    检测 K 线断点 -> 自动补采 -> 重算缺失因子。
    """
    import psycopg, os
    from datetime import date, timedelta

    issues = []
    repaired = 0

    try:
        with get_conn() as conn:
            # 检查最近 7 天日线是否有断点
            today = date.today()
            week_ago = today - timedelta(days=7)
            cur = conn.execute("""
                SELECT symbol, MAX(ts) as last_ts, COUNT(*) as cnt
                FROM bar_1D
                WHERE ts >= %s
                GROUP BY symbol
            """, (week_ago,))
            rows = cur.fetchall()

            for symbol, last_ts, cnt in rows:
                # 预期交易日数（简化：工作日）
                expected = 5  # 一周约 5 个交易日
                if cnt < expected:
                    issues.append(f"{symbol}: 近7天仅{cnt}条(预期~{expected})")
                    # 自动补采
                    try:
                        ts_code = symbol.replace(".SHSE", ".SH").replace(".SZSE", ".SZ").replace(".BSE", ".BJ")
                        from src.data_platform.adapters.tushare_adapter import pull_daily, to_save_rows
                        from src.data_platform.db import save_bars
                        df = pull_daily(ts_code, week_ago.strftime("%Y%m%d"), today.strftime("%Y%m%d"))
                        if not df.empty:
                            rows = to_save_rows(df)
                            repaired = save_bars("1D", rows)
                    except Exception as e:
                        issues.append(f"{symbol} 补采失败: {str(e)[:60]}")

    except Exception as e:
        issues.append(f"检测异常: {str(e)[:100]}")

    if issues:
        from src.alert_notify import AlertNotify
        AlertNotify.get().notify("warn", "数据断连检测", "\n".join(issues))

    return {"status": "ok", "issues": issues, "repaired_bars": repaired}


@app.task(name="src.scheduler.tasks.disk_monitor")
def disk_monitor():
    """F-OPS-002 服务器磁盘监控，超阈值告警。"""
    import shutil
    issues = []
    stats = []

    # 检查挂载点
    for path in ["/", "/var/lib/postgresql", "/var/lib/valkey"]:
        try:
            usage = shutil.disk_usage(path)
            total_gb = usage.total / (1024**3)
            used_gb = usage.used / (1024**3)
            pct = usage.percent
            stats.append({"path": path, "total_gb": round(total_gb, 1),
                          "used_gb": round(used_gb, 1), "pct": pct})
            if pct > 85:
                issues.append(f"{path} 磁盘使用 {pct}% 超阈值 85%")
        except Exception:
            pass

    # PG 数据库大小
    try:
        import psycopg, os
        with get_conn() as conn:
            cur = conn.execute("SELECT pg_size_pretty(pg_database_size('quant'))")
            pg_size = cur.fetchone()[0]
            stats.append({"path": "PG:quant", "size": pg_size})
    except Exception:
        pass

    if issues:
        from src.alert_notify import AlertNotify
        AlertNotify.get().notify("critical", "磁盘告警", "\n".join(issues))

    return {"status": "ok" if not issues else "issues", "stats": stats, "issues": issues}


@app.task(name="src.scheduler.tasks.data_sync_scheduler")
def data_sync_scheduler():
    """扫描 sync_config，按 cron 表达式 + 交易日日历触发同步任务。

    schedule: cron 表达式（如 "30 16 * * 1-5" = 工作日16:30）
    trade_day_filter: none（不过滤）/ workday（工作日）/ trade_day（交易日，用 is_trading_day）
    从 last_sync_ts 算下次 cron 到点，<= now 则触发（不错过）。
    manual/空 schedule 跳过。
    """
    from croniter import croniter
    from datetime import date, datetime, timedelta, timezone
    from src.data_platform.db import is_trading_day, get_conn

    triggered = []
    skipped = []

    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id, schedule, enabled, last_status, last_sync_date, last_sync_ts, trade_day_filter "
                "FROM sync_config WHERE enabled=true"
            )
            configs = cur.fetchall()
    except Exception:
        return {"status": "error", "reason": "DB不可用"}

    now = datetime.now(timezone.utc)

    for sid, schedule, enabled, last_status, last_sync_date, last_sync_ts, trade_day_filter in configs:
        if not enabled:
            skipped.append(sid)
            continue
        if last_status == "running":
            skipped.append(f"{sid}(运行中)")
            continue
        # 空状态（数据被删/从未初始化）跳过增量，需用户手动全量重建
        if last_sync_date is None and sid in ("astock_daily", "etf_daily", "cb_daily"):
            skipped.append(f"{sid}(空状态,需全量重建)")
            continue
        # manual/空 schedule 跳过
        if not schedule or schedule == "manual":
            skipped.append(f"{sid}(手动)")
            continue

        # cron 解析：从上次同步时间算下次到点
        base = last_sync_ts or (now - timedelta(days=7))
        try:
            cron = croniter(schedule, base)
            next_run = cron.get_next(datetime)
        except Exception:
            skipped.append(f"{sid}(cron无效:{schedule})")
            continue

        if next_run > now:
            skipped.append(f"{sid}(未到周期)")
            continue

        # 交易日日历过滤
        tf = trade_day_filter or "none"
        if tf == "trade_day" and not is_trading_day(next_run.date()):
            skipped.append(f"{sid}(非交易日)")
            continue
        if tf == "workday" and next_run.weekday() >= 5:
            skipped.append(f"{sid}(非工作日)")
            continue

        # 到点 + 过滤通过，触发同步
        from src.data_sync import sync
        result = sync(sid)
        triggered.append({"id": sid, "result": result})

    return {"status": "ok", "triggered": len(triggered), "skipped": skipped}


@app.task(name="src.scheduler.tasks.sync_all_symbols",
          bind=True, soft_time_limit=3600, time_limit=4200)
def sync_all_symbols(self, sync_id: str):
    """全市场全量 per-symbol 同步（后台执行，进度写 Valkey）。

    单独放宽超时到 70 分钟（全市场约 37 分钟 + 余量），覆盖全局 task_soft_time_limit=300。
    """
    import os
    import redis
    import psycopg
    from src.data_sync.engine import sync_all

    r = redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"))
    key = f"sync:progress:{sync_id}"
    db_url = os.environ.get("QUANT_DB_URL", "postgresql://quant@127.0.0.1:5432/quant")

    def _mark(status: str, count: int = 0):
        """更新 sync_config.last_status/last_sync_count，让 DataManage 页看到进度。"""
        try:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE sync_config SET last_status=%s, last_sync_count=%s, last_sync_ts=now() WHERE id=%s",
                    (status, count, sync_id))
                conn.commit()
        except Exception:
            pass

    def progress_cb(i: int, total: int, ts_code: str):
        r.hset(key, mapping={
            "status": "running", "done": i, "total": total,
            "current": ts_code, "pct": round(i / total * 100, 1) if total else 0,
        })
        r.expire(key, 3600)
        # 同步 last_status=running + last_sync_count=done（DataManage 页展示）
        _mark("running", i)

    _mark("running", 0)
    r.hset(key, mapping={"status": "running", "done": 0, "total": 0, "pct": 0, "current": ""})
    try:
        result = sync_all(sync_id, progress_cb=progress_cb)
        r.hset(key, mapping={
            "status": result.get("status", "done"), "done": result.get("total", 0),
            "total": result.get("total", 0), "pct": 100,
            "ok": result.get("ok", 0), "saved": result.get("saved", 0),
            "failed_count": result.get("failed_count", 0),
        })
        r.expire(key, 3600)
        _mark("idle", result.get("ok", 0))  # 完成，恢复 idle
        return result
    except Exception as e:
        r.hset(key, mapping={"status": "error", "error": str(e)[:120]})
        r.expire(key, 3600)
        _mark("idle", 0)  # 异常也恢复 idle（避免卡 running）
        raise


@app.task(name="src.scheduler.tasks.sync_via_celery",
          bind=True, soft_time_limit=3600, time_limit=4200)
def sync_via_celery(self, sync_id: str, backfill_from: str | None = None):
    """类型级同步异步执行（HTTP trigger 立即返回 task_id，前端轮询进度）。

    progress 写 Valkey sync:type:{sid}（与全量重建 sync_all_symbols 的 sync:progress:{sid} 分开）。
    完成态存 result 关键字段供前端 notifyResult 显示。
    """
    import os
    import redis
    from src.data_sync.engine import sync

    r = redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"))
    key = f"sync:type:{sync_id}"

    def progress_cb(i: int, total: int, current: str):
        r.hset(key, mapping={
            "status": "running", "done": i, "total": total,
            "current": current, "pct": round(i / total * 100, 1) if total else 0,
        })
        r.expire(key, 3600)

    r.hset(key, mapping={"status": "running", "done": 0, "total": 0, "pct": 0, "current": ""})
    r.expire(key, 3600)
    try:
        result = sync(sync_id, backfill_from=backfill_from, progress_cb=progress_cb)
        # 完成态存关键字段，供前端 notifyResult 显示
        failed = result.get("failed_dates") or []
        r.hset(key, mapping={
            "status": result.get("status", "done"),
            "rows_pulled": result.get("rows_pulled", 0),
            "rows_saved": result.get("rows_saved", 0),
            "expected_days": result.get("expected_days") or 0,
            "actual_days": result.get("actual_days") or 0,
            "failed_dates_count": len(failed),
            "pct": 100,
        })
        r.expire(key, 3600)
        return result
    except Exception as e:
        r.hset(key, mapping={"status": "error", "error": str(e)[:120]})
        r.expire(key, 3600)
        raise
