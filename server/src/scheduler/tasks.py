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


def _is_trading_hours() -> bool:
    """A 股连续竞价时段（9:30-11:30, 13:00-15:00）。非交易日+非时段返回 False。"""
    if not _is_trading_day():
        return False
    now = datetime.now()
    hm = now.hour * 100 + now.minute
    return (930 <= hm <= 1130) or (1300 <= hm <= 1500)


@app.task(name="src.scheduler.tasks.data_increment_daily", bind=True, max_retries=2)
def data_increment_daily(self):
    """[已弃用] 盘后增量更新日线。改用 data_sync_scheduler + sync_config DB 驱动（P3-16）。保留 beat 仅兼容。"""
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

    from src.alert_notify import report
    report("盘后报告", body)
    return {"status": "ok", "body": body[:200]}


@app.task(name="src.scheduler.tasks.astock_minute_analysis")
def astock_minute_analysis():
    """盘中分钟研判；非交易日/连续竞价外跳过。P1-6 接线：读 bar_1min 最新 + MinuteAnalysisEngine + 落 PG。"""
    if not _is_trading_hours():
        return {"status": "skipped", "reason": "非交易时段"}
    # P1-6：读 bar_1min 最新 bar + MinuteAnalysisEngine.on_bar + 落 astock_analysis 表
    try:
        from src.astock_analysis.analysis import MinuteAnalysisEngine
        from src.data_platform.db import get_bars, get_conn
        # 取活跃策略的 symbol
        with get_conn() as conn:
            cur = conn.execute("SELECT symbol FROM strategy_config WHERE enabled=true AND type='astock_analysis' LIMIT 5")
            symbols = [r[0] for r in cur.fetchall()]
        if not symbols:
            return {"status": "skipped", "reason": "无活跃 A 股策略"}
        engine = MinuteAnalysisEngine()
        results = []
        for sym in symbols:
            bars_df = get_bars(sym, "1min", None, None)
            if bars_df is None or bars_df.empty or len(bars_df) < 20:
                continue
            history = bars_df.tail(20).to_dict("records")
            bar = history[-1]
            r = engine.on_bar(bar, history[:-1])
            if r:
                results.append({"symbol": sym, "action": r["action"], "score": r["score"], "rating": r["rating"]})
                # 落 PG
                with get_conn() as conn:
                    conn.execute("SELECT 1 FROM astock_analysis LIMIT 1")
                    import json
                    conn.execute("INSERT INTO astock_analysis (symbol, action, score, rating, factors) VALUES (%s,%s,%s,%s,%s)",
                                 (sym, r["action"], r["score"], r["rating"], json.dumps(r.get("factors", {}))))
                    conn.commit()
        return {"status": "ok", "count": len(results), "results": results}
    except Exception as e:
        logger.exception("astock_minute_analysis 失败")
        return {"status": "error", "reason": str(e)[:100]}


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
        from src.alert_notify import notify
        notify("critical", "system", "接口健康异常", f"离线: {errors}")

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

    # P4-7 drift_check 实现：取当日 astock_analysis 结果 vs 回测同时段因子比对
    issues = []
    try:
        with get_conn() as conn:
            # 取最新实盘分析
            cur = conn.execute("""SELECT symbol, score, rating, factors FROM astock_analysis
                WHERE ts::date = current_date ORDER BY ts DESC""")
            live_rows = cur.fetchall()
            if not live_rows:
                return {"status": "skipped", "reason": "今日无实盘分析数据"}
            # 逐标的比对回测结果（简化：score 偏差 >0.5 告警）
            for symbol, live_score, live_rating, live_factors in live_rows:
                cur2 = conn.execute(
                    "SELECT result FROM backtest_symbols WHERE symbol=%s AND status='done' ORDER BY id DESC LIMIT 1",
                    (symbol,))
                bt = cur2.fetchone()
                if bt and bt[0]:
                    import json
                    bt_data = json.loads(bt[0])
                    bt_score = bt_data.get('total_return_pct', 0)
                    if abs(live_score - bt_score) > 0.5:
                        issues.append(f"{symbol}: 实盘 score {live_score:.3f} vs 回测 {bt_score:.3f} 偏差 >0.5")
    except Exception as e:
        issues.append(f"drift_check 异常: {str(e)[:80]}")

    if issues:
        from src.alert_notify import notify
        notify("critical", "risk", "因子漂移告警", f"实盘-回测因子偏差超限: {issues}")

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
            # 校验表存在
            try:
                conn.execute("SELECT 1 FROM signal_log LIMIT 1")
            except Exception:
                pass
            try:
                conn.execute("SELECT 1 FROM order_log LIMIT 1")
            except Exception:
                pass
            try:
                conn.execute("SELECT 1 FROM trade_log LIMIT 1")
            except Exception:
                pass
            conn.commit()

            # 4. ST2 持仓双源 diff（2026-08-18，N 建议：快照真相 vs trade_log 推导=账实分离持续验证器）
            try:
                # O-F2：两侧符号命名空间不同（快照=vt_symbol "600000.SSE" vs trade_log=裸 "600000"）
                # ——join 前必须归一，否则永不命中→每小时误报
                cur = conn.execute("""
                    SELECT COALESCE(s.sym, t.sym) AS sym,
                           COALESCE(s.snap_vol, 0) AS snap_vol,
                           COALESCE(t.derived_vol, 0) AS derived_vol
                    FROM (SELECT split_part(symbol, '.', 1) AS sym, SUM(volume) AS snap_vol
                          FROM position_snapshot WHERE direction != 'short' GROUP BY 1) s
                    FULL OUTER JOIN (
                        SELECT split_part(symbol, '.', 1) AS sym,
                               SUM(CASE WHEN action='BUY' THEN volume ELSE -volume END) AS derived_vol
                        FROM trade_log GROUP BY 1) t ON s.sym = t.sym
                    WHERE COALESCE(s.snap_vol, 0) != COALESCE(t.derived_vol, 0)""")
                for sym, sv, dv in cur.fetchall():
                    # O-S2：trade_log 全历史推导（含上线前底仓/场外单）天然有持续差异——
                    # 展示给对账页（issues）即可，归因与处置靠人；不加码告警频率
                    issues.append(f"持仓账实分离: {sym} 券商快照={sv} trade_log推导={dv}")
            except Exception:
                pass   # 表未就绪静默（与上方三表探测一致，O-S2）

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
        from src.alert_notify import notify
        notify("critical", "risk", "三账对账异常", "\n".join(issues))

    return {"status": "ok" if not issues else "issues", "issues": issues}


@app.task(name="src.scheduler.tasks.data_continuity_check")
def data_continuity_check():
    """P-MON-006 数据断连自愈与断点补采。

    检测 K 线断点 -> 自动补采 -> 重算缺失因子。
    断线检测（Valkey 心跳）+ 因子重算触发补采。
    """
    import psycopg, os, redis
    from datetime import date, timedelta

    issues = []
    repaired = 0
    reconnected = 0
    r = None

    # 1. 断线检测（Valkey 心跳网关）
    try:
        r = redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
                                 socket_timeout=3, decode_responses=True)
        beat = r.get("heartbeat:gateway")
        if beat is None:
            reconnected += 1
            issues.append("Valkey 网关心跳丢失，尝试重建连接")
            # 心跳重建由各网关 _init 时写入，此处只检测
    except Exception as e:
        issues.append(f"Valkey 检测异常: {str(e)[:60]}")

    # 2. K 线断点检测 + 补采（已有逻辑）
    try:
        with get_conn() as conn:
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
                from src.data_platform.db import is_trading_day as _is_td
                _days = [week_ago + timedelta(days=i) for i in range((today - week_ago).days + 1)]
                expected = sum(1 for d in _days if d.weekday() < 5 and _is_td(d))
                if cnt < expected:
                    issues.append(f"{symbol}: 近7天仅{cnt}条(预期~{expected})")
                    try:
                        ts_code = symbol.replace(".SHSE", ".SH").replace(".SZSE", ".SZ").replace(".BSE", ".BJ")
                        from src.data_platform.adapters.tushare_adapter import pull_daily, to_save_rows
                        from src.data_platform.db import save_bars
                        df = pull_daily(ts_code, week_ago.strftime("%Y%m%d"), today.strftime("%Y%m%d"))
                        if not df.empty:
                            rows = to_save_rows(df)
                            repaired = save_bars("1D", rows)
                            # 3. 因子重算触发：有修复则标记（后续 astock_select_daily 将利用完整数据）
                            if repaired > 0:
                                if r is not None:
                                    r.set(f"factor:recalc:triggered", "1", ex=3600)
                    except Exception as e:
                        issues.append(f"{symbol} 补采失败: {str(e)[:60]}")
    except Exception as e:
        issues.append(f"检测异常: {str(e)[:100]}")

    if issues:
        from src.alert_notify import notify
        notify("warn", "data", "数据断连检测", "\n".join(issues))

    return {"status": "ok", "issues": issues, "repaired_bars": repaired, "reconnected": reconnected}


@app.task(name="src.scheduler.tasks.adj_factor_backfill_task",
          bind=True, soft_time_limit=7200, time_limit=7500)   # F-F1：全量 ~50min，留余量；被杀可重触发续填
def adj_factor_backfill_task(self, start_date: str | None = None, end_date: str | None = None):
    """复权因子回填（A/B-F1：bar_1D 历史全 NULL）。手动触发（积分到账后），降级即返回不抛。"""
    import redis as _redis, os as _os
    from src.data_sync.engine import backfill_adj_factor
    from src.task_manager import create_task, update_heartbeat, complete_task
    task_id = self.request.id
    create_task(task_id, "复权因子回填", "sync", "manual", "system",
                {"start": start_date, "end": end_date})
    r = _redis.from_url(_os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"))
    key = "sync:adj-factor"

    def progress_cb(i: int, total: int, current: str):
        r.hset(key, mapping={"status": "running", "done": i, "total": total, "current": current,
                             "pct": round(i / total * 100, 1) if total else 0})
        r.expire(key, 7200)
        update_heartbeat(task_id, {"current": i, "total": total, "step": current})

    try:
        result = backfill_adj_factor(start_date, end_date, progress_cb=progress_cb)
        result["rows_saved"] = result.pop("updated", 0)
        r.hset(key, mapping={"status": result["status"],
                             "done": result.get("processed", 0), "total": result.get("days", 0)})
        r.expire(key, 7200)
        complete_task(task_id, "completed" if result["status"] == "success" else "failed",
                      None if result["status"] == "success" else str(result.get("reason", ""))[:200])
        return result
    except Exception as e:
        r.hset(key, mapping={"status": "error", "error": str(e)[:100]})
        r.expire(key, 7200)
        complete_task(task_id, "failed", str(e)[:200])
        raise


@app.task(name="src.scheduler.tasks.health_monitor_check")
def health_monitor_check():
    """15-服务监控：30s 采集判定（unit 状态/依赖/心跳 + 沿检测 + health_event 落库 + 告警）。

    S6 修订的配套观测面——断流类问题只告警不动作，这里是告警的聚合点之一。
    """
    from src.health_monitor.monitor import run_check
    return run_check()


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
        from src.alert_notify import notify
        notify("critical", "system", "磁盘告警", "\n".join(issues))

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
        if last_status == "running":
            skipped.append(f"{sid}(运行中)")
            continue
        # 空状态（数据被删/从未初始化）跳过增量，需用户手动全量重建
        if last_sync_date is None and sid in ("astock_daily", "etf_daily", "cb_daily",
                                               "astock_minute", "astock_minute_5min"):
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
    from src.task_manager import create_task, update_heartbeat, complete_task, log_task, notify_on_failure
    task_id = self.request.id
    create_task(task_id, f"同步 {sync_id}", "sync", "manual", "system",
                {"sync_id": sync_id, "backfill_from": backfill_from})

    r = redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"))
    key = f"sync:type:{sync_id}"

    def progress_cb(i: int, total: int, current: str):
        r.hset(key, mapping={
            "status": "running", "done": i, "total": total,
            "current": current, "pct": round(i / total * 100, 1) if total else 0,
        })
        r.expire(key, 3600)
        update_heartbeat(task_id, {"current": i, "total": total,
                                    "pct": round(i / total * 100, 1) if total else 0, "step": current})

    r.hset(key, mapping={"status": "running", "done": 0, "total": 0, "pct": 0, "current": ""})
    update_heartbeat(task_id, {"current": 0, "total": 0, "pct": 0, "step": "init"})
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
        complete_task(task_id, status="completed")
        return result
    except Exception as e:
        r.hset(key, mapping={"status": "error", "error": str(e)[:120]})
        r.expire(key, 3600)
        complete_task(task_id, status="failed", error=str(e)[:200])
        log_task(task_id, "ERROR", f"同步失败: {e}")
        notify_on_failure(f"同步失败 {sync_id}", str(e)[:200])
        raise

@app.task(name="src.scheduler.tasks.task_stuck_check")
def task_stuck_check():
    """卡死检测巡检：last_heartbeat 超时 + running -> stuck（PT1）。"""
    from src.task_manager import detect_stuck
    count = detect_stuck()
    return {"stuck_count": count}


# ====================================================================
# 回测组任务（B3 #1）：run 分发 + symbol 子任务 + Valkey pub
# ====================================================================

@app.task(name="src.scheduler.tasks.backtest_run_task",
          bind=True, soft_time_limit=3600, time_limit=4200)
def backtest_run_task(self, run_id: int):
    """回测组任务：读 run + 写 backtest_symbols pending + 按 mode 分发子任务（B3）。"""
    import json
    from src.task_manager import create_task, update_heartbeat, complete_task
    task_id = self.request.id
    create_task(task_id, f"回测 run {run_id}", "backtest", "manual", "system", {"run_id": run_id})

    with get_conn() as conn:
        cur = conn.execute(
            "SELECT strategy_config_id, symbols, params, mode FROM backtest_runs WHERE id=%s", (run_id,))
        r = cur.fetchone()
    if not r:
        complete_task(task_id, status="failed", error="run 不存在")
        return {"status": "error", "error": "run 不存在"}
    strat_id, symbols_json, params_json, mode = r
    symbols = json.loads(symbols_json)

    with get_conn() as conn:
        for sym in symbols:
            conn.execute(
                "INSERT INTO backtest_symbols (run_id, symbol, status) VALUES (%s,%s,'pending') "
                "ON CONFLICT (run_id, symbol) DO NOTHING", (run_id, sym))
        conn.execute("UPDATE backtest_runs SET status='running', task_id=%s WHERE id=%s", (task_id, run_id))
        conn.commit()
    update_heartbeat(task_id, {"step": "dispatching", "total": len(symbols)})

    # 按 mode 分发
    if mode == "single" or len(symbols) == 1:
        for sym in symbols:
            backtest_symbol_task.delay(run_id, sym)
    elif mode == "parallel":
        from celery import group
        group([backtest_symbol_task.s(run_id, s) for s in symbols])()
    elif mode == "serial":
        from celery import chain
        chain(*[backtest_symbol_task.s(run_id, s) for s in symbols])()
    return {"status": "running", "run_id": run_id, "symbols": len(symbols)}


@app.task(name="src.scheduler.tasks.backtest_symbol_task",
          bind=True, soft_time_limit=3600, time_limit=4200)
def backtest_symbol_task(self, run_id: int, symbol: str):
    """单标的回测子任务：跑 BacktestEngine + on_bar publish Valkey + 存 result（B3）。"""
    import json, os, redis
    from datetime import date, timedelta
    from src.data_platform.db import get_bars
    from src.strategy_framework.strategy import StrategyConfig
    from src.strategy_framework.backtest import BacktestEngine

    r = redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"))
    pub_key = f"backtest:run:{run_id}:{symbol}"

    with get_conn() as conn:
        cur = conn.execute("SELECT strategy_config_id, params, symbol_params FROM backtest_runs WHERE id=%s", (run_id,))
        rr = cur.fetchone()
        if rr:
            cur = conn.execute("SELECT factors, aggregator, params FROM strategy_config WHERE id=%s", (rr[0],))
            sc = cur.fetchone()
    if not rr or not sc:
        r.set(pub_key + ":error", "策略/run 不存在")
        return {"status": "error", "error": "策略/run 不存在"}

    params = json.loads(rr[1])
    # per-symbol 参数覆盖（symbol_params 列，可能为 NULL）
    symbol_params_all = json.loads(rr[2]) if rr[2] else {}
    per_symbol = symbol_params_all.get(symbol, {}) if isinstance(symbol_params_all, dict) else {}
    start = params.get("start", (date.today() - timedelta(days=365)).isoformat())
    end = params.get("end", date.today().isoformat())
    bars_df = get_bars(symbol, "1D", start, end)
    bars = bars_df.to_dict("records") if not bars_df.empty else []

    # 合并参数：策略级 params + per-symbol 覆盖
    strategy_params = json.loads(sc[2]) if sc[2] else {}
    merged_params = {**strategy_params, **per_symbol}
    # 移除 parameter_defs 元数据（不是参数值）
    merged_params.pop("parameter_defs", None)

    cfg = StrategyConfig(
        id=rr[0], name=str(rr[0]), type="astock_analysis", symbol=symbol, adapter="xtp",
        factors=json.loads(sc[0]) if sc[0] else [],
        aggregator=json.loads(sc[1]) if sc[1] else {},
        params=merged_params)

    engine = BacktestEngine(
        initial_capital=params.get("capital", 100000),
        commission_rate=params.get("commission", 0.0005),
        slippage=params.get("slippage", 0))

    def on_bar_cb(bar, ctx):
        r.set(pub_key, json.dumps({
            "progress": ctx["progress"],
            "bar": {"ts": str(bar.get("ts"))[:19], "open": bar.get("open"), "high": bar.get("high"),
                    "low": bar.get("low"), "close": bar.get("close"), "volume": bar.get("volume")},
            "equity": ctx["equity"], "position": ctx["position"],
            "avg_price": ctx.get("avg_price"), "trades": ctx["trades"],
        }), ex=3600)

    try:
        result = engine.run(cfg, bars, on_bar_callback=on_bar_cb)
        result_json = json.dumps({
            "total_return_pct": result.total_return_pct, "win_rate": result.win_rate,
            "max_drawdown_pct": result.max_drawdown_pct, "sharpe_ratio": result.sharpe_ratio,
            "total_trades": result.total_trades, "daily_values": result.daily_values,
            "trades": result.trades, "metrics": result.metrics})
        with get_conn() as conn:
            conn.execute(
                "UPDATE backtest_symbols SET status='done', result=%s WHERE run_id=%s AND symbol=%s",
                (result_json, run_id, symbol))
            cur = conn.execute(
                "SELECT count(*) FROM backtest_symbols WHERE run_id=%s AND status!='done'", (run_id,))
            pending = cur.fetchone()[0]
            if pending == 0:
                conn.execute("UPDATE backtest_runs SET status='done', finished_at=now() WHERE id=%s", (run_id,))
            conn.commit()
        r.set(pub_key + ":done", result_json, ex=3600)
        return {"status": "done", "symbol": symbol, "return": result.total_return_pct}
    except Exception as e:
        with get_conn() as conn:
            conn.execute(
                "UPDATE backtest_symbols SET status='error', result=%s WHERE run_id=%s AND symbol=%s",
                (json.dumps({"error": str(e)[:200]}), run_id, symbol))
            conn.commit()
        r.set(pub_key + ":error", str(e)[:200], ex=3600)
        return {"status": "error", "symbol": symbol, "error": str(e)[:200]}


# ====================================================================
# Phase 1 小任务（D3 条款同步 / D5 定时闹钟 / #37 通道监控）
# ====================================================================

@app.task(name="src.scheduler.tasks.convertible_terms_sync")
def convertible_terms_sync():
    """D3 可转债条款数据同步（盘后，每日一次）。拉取活跃可转债基本信息并存 DB。"""
    from src.data_platform.adapters.tushare_adapter import pull_convertible_bonds, pull_cb_basic
    import json
    try:
        bonds = pull_convertible_bonds()
    except Exception as e:
        return {"status": "error", "reason": f"pull_convertible_bonds 失败: {e}"}
    results = []
    for ts_code in bonds[:50]:
        try:
            terms = pull_cb_basic(ts_code)
            if terms:
                with get_conn() as conn:
                    conn.execute("SELECT 1 FROM convertible_terms LIMIT 1")
                    conn.execute(
                        "INSERT INTO convertible_terms (ts_code, terms, updated_at) VALUES (%s,%s,now()) "
                        "ON CONFLICT (ts_code) DO UPDATE SET terms=EXCLUDED.terms, updated_at=now()",
                        (ts_code, json.dumps(terms, ensure_ascii=False)))
                    conn.commit()
                results.append(ts_code)
        except Exception as e:
            logger.warning("convertible_terms_sync 处理 %s 失败: %s", ts_code, e)
            continue
    return {"status": "ok", "count": len(results)}


@app.task(name="src.scheduler.tasks.budget_alert_check")
def budget_alert_check():
    """D5 定时预算告警检查（每小时，交易时段内）。"""
    if not _is_trading_hours():
        return {"status": "skipped", "reason": "非交易时段"}
    try:
        from src.web_api.main import check_budget_alerts
        result = check_budget_alerts()
        return {"status": "ok", "alerts": len(result.get("alerts", []))}
    except Exception as e:
        return {"status": "error", "reason": str(e)[:200]}



@app.task(name="src.scheduler.tasks.static_list_sync")
def static_list_sync():
    """F-DATA-004 静态标的清单同步（定期拉 stock_basic + 标记退市）。"""
    try:
        from src.data_platform.adapters.tushare_adapter import get_pro
        pro = get_pro()
    except Exception:
        return {"status": "skipped", "reason": "tushare 未配"}
    synced = 0
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM static_symbols LIMIT 1")
            df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name,industry")
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    conn.execute(
                        "INSERT INTO static_symbols (ts_code,name,industry,list_status,delisted) VALUES (%s,%s,%s,'L',false) "
                        "ON CONFLICT (ts_code) DO UPDATE SET name=EXCLUDED.name,industry=EXCLUDED.industry,"
                        "list_status='L',delisted=false,updated_at=now()",
                        (row["ts_code"], row.get("name",""), row.get("industry","")))
                    synced += 1
            conn.commit()
    except Exception as e:
        logger.warning(f"static_list_sync 失败: {e}")
        return {"status": "error", "reason": str(e)[:100]}
    return {"status": "ok", "synced": synced}

@app.task(name="src.scheduler.tasks.broker_health_check")
def broker_health_check():
    """#37 通道用量监控：检查各 broker 连通性，异常告警。"""
    from src.strategy_framework.broker import _REGISTRY
    from src.alert_notify import notify
    results = {}
    for provider, cls in _REGISTRY.items():
        try:
            broker = cls()
            ok = broker.test_connection()
            results[provider] = {"status": "ok" if ok else "error"}
        except Exception as e:
            results[provider] = {"status": "error", "msg": str(e)[:100]}
    errors = [k for k,v in results.items() if v["status"] == "error"]
    if errors:
        notify("warn", "system", "通道连通异常", f"离线: {errors}")
    return {"status": "ok" if not errors else "issues", "results": results}





@app.task(name="src.scheduler.tasks.email_outbox_sweep")
def email_outbox_sweep():
    """发件箱扫描：重发到期待发邮件（指数退避由 next_attempt_at 控制，beat 每分钟调）。"""
    from src.web_api.email_service import sweep
    try:
        return {"processed": sweep(3)}
    except Exception as e:
        logger.exception(f"email outbox sweep failed: {e}")
        return {"processed": 0, "error": str(e)}


@app.task(name="src.scheduler.tasks.notifications_cleanup")
def notifications_cleanup():
    """通知留存清理（每日）：已确认>7天、全部>30天删除。"""
    from src.alert_notify import cleanup
    try:
        return cleanup()
    except Exception as e:
        logger.exception(f"notifications cleanup failed: {e}")
        return {"error": str(e)}
