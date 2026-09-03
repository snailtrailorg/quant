"""Celery 定时任务实现。"""

from __future__ import annotations
from src.data_platform.db import get_conn
from src.data_platform.tier_tables import TIER1_SYNC_IDS, TIER2_INCREMENTAL_TABLES
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
        notify("critical", "system", "接口健康异常", f"离线: {errors}", code="health.iface-down")

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
            # 逐标的比对回测结果
            # P2 修复（2026-08-20 双盲审计 B4）：原"因子 score vs 回测收益率"量纲错位——
            # 告警恒噪或恒哑。改两维各按量纲阈值：评级背离（方向性）+收益率偏差>15pct。
            for symbol, live_score, live_rating, live_factors in live_rows:
                cur2 = conn.execute(
                    "SELECT result FROM backtest_symbols WHERE symbol=%s AND status='done' ORDER BY id DESC LIMIT 1",
                    (symbol,))
                bt = cur2.fetchone()
                if bt and bt[0]:
                    import json
                    bt_data = json.loads(bt[0])
                    bt_ret = bt_data.get('total_return_pct', 0)
                    # 维度 1：评级背离（回测正收益 vs 实盘 AVOID / 回测负收益 vs 实盘 BUY）
                    if (bt_ret > 5 and live_rating == "AVOID") or (bt_ret < -5 and live_rating == "BUY"):
                        issues.append(f"{symbol}: 评级背离 实盘={live_rating} vs 回测收益 {bt_ret:.1f}%")
                    # 维度 2：因子分异常（|score|>1.5 出界的坏数据面）
                    if abs(live_score) > 1.5:
                        issues.append(f"{symbol}: 实盘因子分出界 {live_score:.3f}")
    except Exception as e:
        issues.append(f"drift_check 异常: {str(e)[:80]}")

    if issues:
        from src.alert_notify import notify
        notify("critical", "risk", "因子漂移告警", f"实盘-回测因子偏差超限: {issues}", code="factor.drift")

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
                    # P1-2（web-design 05 §5.4）：结构化差异单双写（旧字符串兼容期保留，勿误修#11）。
                    # upsert 语义：open 单在位则刷新数量/时间（first_seen 保留）；豁免基准内
                    # （|diff|<=exempt_qty 且豁免期内）不再开新单。
                    try:
                        conn.execute("""
                            INSERT INTO reconcile_issue (symbol, issue_type, detail, broker_qty, derived_qty)
                            SELECT %s, 'position_diff', %s, %s, %s
                            WHERE NOT EXISTS (   -- 终审 P1-5：豁免基准内不开新单（差异较基准扩大才再告警）
                              SELECT 1 FROM reconcile_issue e
                              WHERE e.symbol = %s AND e.issue_type = 'position_diff'
                                AND e.status = 'exempt'
                                AND (e.exempt_until IS NULL OR e.exempt_until >= current_date)
                                AND ABS(%s - %s) <= COALESCE(e.exempt_qty, 0))
                            ON CONFLICT (symbol, issue_type) WHERE status = 'open'
                            DO UPDATE SET broker_qty = EXCLUDED.broker_qty,
                                          derived_qty = EXCLUDED.derived_qty,
                                          detail = EXCLUDED.detail,
                                          updated_at = now()
                            """,
                            (sym, f"券商快照={sv} trade_log推导={dv}", sv, dv, sym, sv, dv))
                    except Exception as _e:
                        logging.getLogger("scheduler").warning("reconcile_issue 双写失败（不阻断）: %s", _e)
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
        notify("critical", "risk", "三账对账异常", "\n".join(issues), code="reconcile.error")

    return {"status": "ok" if not issues else "issues", "issues": issues}


# ── 项 18 三档新表新鲜度检测 ──
# 表清单单一真相源：src/data_platform/tier_tables.py（collector.py 同源，盲审遗留收敛）
# 二档仅 4 张增量表有游标（盲审 A-2：_advance_cursors 只推 incremental 表，
# 其余 6 张非增量表每次全量拉、无游标无 per-table sync_log--由 pool_data 任务心跳覆盖）

_TIER1_THRESHOLD_HOURS = 48   # 日频盘后族，跨周末/节假日容忍
_TIER2_THRESHOLD_HOURS = 192  # 8 天，财务季频大容忍


def _check_tier_freshness() -> list[dict]:
    """三档 19 张新表新鲜度检测。

    一档 9 表：查 sync_log 最新 status='success' 行 ts，超 48h 告警。
    二档 10 表：查 pool_data_cursor 最新游标日期，超 8 天告警。
    异常返回空列表不抛。

    返回 [{"sync_id": str, "last_ts": str|None, "age_hours": float|None, "kind": str}]，
    仅含超阈值条目。
    """
    from datetime import datetime, timezone
    try:
        from src.data_platform.db import get_conn
        stale = []
        now = datetime.now(timezone.utc)

        with get_conn() as conn:
            # 一档：按 sync_id 取最新 success 行 ts
            cur = conn.execute(
                "SELECT DISTINCT ON (sync_id) sync_id, ts "
                "FROM sync_log WHERE sync_id = ANY(%s) AND status = 'success' "
                "ORDER BY sync_id, ts DESC",
                 (TIER1_SYNC_IDS,))
            rows = cur.fetchall()
            seen = {r[0] for r in rows}
            for sid in TIER1_SYNC_IDS:
                if sid in seen:
                    last_ts = rows[[r[0] for r in rows].index(sid)][1]
                    if last_ts.tzinfo is None:
                        last_ts = last_ts.replace(tzinfo=timezone.utc)
                    age_h = (now - last_ts).total_seconds() / 3600
                    if age_h > _TIER1_THRESHOLD_HOURS:
                        stale.append({
                            "sync_id": sid, "last_ts": last_ts.isoformat(),
                            "age_hours": round(age_h, 1), "kind": "tier1"})
                else:
                    stale.append({
                        "sync_id": sid, "last_ts": None,
                        "age_hours": None, "kind": "tier1"})

            # 二档：pool_data_cursor 各表最新游标日期（仅 4 张增量表）
            cur = conn.execute("SELECT table_name, last_pull_date FROM pool_data_cursor")
            cursor_rows = {r[0]: r[1] for r in cur.fetchall()}
            for tbl in TIER2_INCREMENTAL_TABLES:
                last_date = cursor_rows.get(tbl)
                if last_date:
                    last_dt = datetime.strptime(last_date, "%Y%m%d").replace(tzinfo=timezone.utc)
                    age_h = (now - last_dt).total_seconds() / 3600
                    if age_h > _TIER2_THRESHOLD_HOURS:
                        stale.append({
                            "sync_id": f"pool_data:{tbl}", "last_ts": last_dt.isoformat(),
                            "age_hours": round(age_h, 1), "kind": "tier2"})
                else:
                    stale.append({
                        "sync_id": f"pool_data:{tbl}", "last_ts": None,
                        "age_hours": None, "kind": "tier2"})

            # 二档任务心跳（盲审 A-2）：非增量 6 表无游标，由 pool_data 任务整体
            # done 心跳覆盖（任务 done = 全部 10 表都拉过一轮）
            cur = conn.execute(
                "SELECT ts FROM sync_log WHERE sync_id='pool_data' AND status='done' "
                "ORDER BY ts DESC LIMIT 1")
            row = cur.fetchone()
            if row and row[0]:
                pd_ts = row[0] if row[0].tzinfo else row[0].replace(tzinfo=timezone.utc)
                age_h = (now - pd_ts).total_seconds() / 3600
                if age_h > _TIER2_THRESHOLD_HOURS:
                    stale.append({
                        "sync_id": "pool_data", "last_ts": pd_ts.isoformat(),
                        "age_hours": round(age_h, 1), "kind": "tier2"})
            else:
                stale.append({
                    "sync_id": "pool_data", "last_ts": None,
                    "age_hours": None, "kind": "tier2"})

        return stale
    except Exception as e:
        logger.warning("三档新鲜度检测异常（降级跳过）: %s", e)
        return []


def _tier_alert_filter(stale: list[dict]) -> tuple[list[dict], set[str]]:
    """tier stale 状态翻转过滤（盲审遗留 2026-08-22）。

    检测 1h 一跑、notify 去重仅 60s：持续 stale 会逐小时重报（每天 24 条噪音/表）。
    改电平语义：仅「新变 stale」的条目参与告警；全部恢复时返回恢复集（发一条恢复行）。
    状态存 Valkey（无 TTL）；Valkey 不可用 fail-open 退回全量报（告警宁可重复不可丢）。

    返回 (参与告警的条目, 恢复的 sync_id 集合)。
    """
    import os
    import redis
    key = "tier_stale:prev"
    try:
        r = redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
                                 socket_timeout=3, decode_responses=True)
        prev = set(r.smembers(key))
        cur = {t["sync_id"] for t in stale}
        r.delete(key)
        if cur:
            r.sadd(key, *cur)
        return [t for t in stale if t["sync_id"] not in prev], prev - cur
    except Exception as e:
        logger.warning("tier 告警状态存取失败（fail-open 全量报）: %s", e)
        return stale, set()


@app.task(name="src.scheduler.tasks.data_continuity_check")
def data_continuity_check():
    """P-MON-006 数据断连自愈与断点补采。

    检测 K 线断点 -> 自动补采 -> 重算缺失因子。
    断线检测（Valkey 心跳）+ 因子重算触发补采。
    三档新表新鲜度检测（项 18，2026-08-21）。
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

    # 2. 三档新表新鲜度检测（项 18，2026-08-21；状态翻转告警 2026-08-22）
    tier_stale, tier_recovered = _tier_alert_filter(_check_tier_freshness())
    for t in tier_stale:
        sid = t["sync_id"]
        last = t["last_ts"] or "从未同步"
        age = f"{t['age_hours']}h" if t['age_hours'] else "N/A"
        issues.append(f"[{t['kind']}] {sid}: 最新={last}, 距今={age}")
    if tier_recovered:
        issues.append(f"[tier] 新鲜度恢复: {', '.join(sorted(tier_recovered))}")

    # 3. K 线断点检测 + 补采（已有逻辑）
    # DB 优化（2026-08-21 盘点重灾 #2）：原 SELECT 开事务后循环内逐标的 Tushare 网络拉取+补写
    # ——事务悬挂到函数尾随标的数放大（idle in transaction + 事务跨网络）。改：检测查询先
    # 关连接（fetchall 后块内无活事务），补采的 save_bars 各自短事务。
    # P1 顺带修（审计 B5 当日必误报）：expected 原含"今天"——盘中跑恒报"缺今天"。
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

        from src.data_platform.db import is_trading_day as _is_td
        _days = [week_ago + timedelta(days=i) for i in range((today - week_ago).days)]
        expected = sum(1 for d in _days if d.weekday() < 5 and _is_td(d))   # 不含今天
        for symbol, last_ts, cnt in rows:
            if cnt < expected:
                issues.append(f"{symbol}: 近7天仅{cnt}条(预期~{expected})")
                try:
                    ts_code = symbol.replace(".SHSE", ".SH").replace(".SZSE", ".SZ").replace(".BSE", ".BJ")
                    from src.data_platform.adapters.tushare_adapter import pull_daily, to_save_rows
                    from src.data_platform.db import save_bars
                    df = pull_daily(ts_code, week_ago.strftime("%Y%m%d"), today.strftime("%Y%m%d"))
                    if not df.empty:
                        rws = to_save_rows(df)
                        repaired = save_bars("1D", rws)
                        # 3. 因子重算触发：有修复则标记（后续 astock_select_daily 将利用完整数据）
                        if repaired > 0:
                            if r is not None:
                                # F-55（2026-09-03）：写变化值（原常量 "1"）——多 live-task worker
                                # 各记 last_seen，常量值第二次触发无法区分。isoformat 微秒级唯一。
                                r.set("factor:recalc:triggered", datetime.now().isoformat(), ex=3600)
                except Exception as e:
                    issues.append(f"{symbol} 补采失败: {str(e)[:60]}")
    except Exception as e:
        issues.append(f"检测异常: {str(e)[:100]}")

    if issues:
        from src.alert_notify import notify
        notify("warn", "data", "数据断连检测", "\n".join(issues), code="data.disconn")

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


@app.task(name="src.scheduler.tasks.pool_data_sync_task",
          bind=True, soft_time_limit=320, time_limit=350)
def pool_data_sync_task(self, full=False, symbols=None):
    """池内深度数据同步（三档第二档，2026-08-19）。独立于已禁用的分钟同步。

    full=True 全量校准（无视游标窗口，游标照常推进）——周日 beat 自动 + 手动定期跑。
    symbols=[ts_code...] 定向回补（入池触发）：无窗口全量、不推进游标。
    回补/校准撞 SyncLock 有限重试（O 复审 G1：一次性触发丢一轮=校准丢一周，不重试不可接受）。
    """
    from src.data_sync.pool_data import sync_pools_data
    result = sync_pools_data(full=full, symbols=symbols)
    if result.get("status") == "skipped" and (full or symbols):
        raise self.retry(countdown=60, max_retries=5)
    return result


@app.task(name="src.scheduler.tasks.pool_minute_sync_task",
          bind=True, soft_time_limit=320, time_limit=350)
def pool_minute_sync_task(self):
    """池驱动分钟同步（S+T 审 2026-08-19：engine 编排+scheduler 薄壳第 4 例）。

    每 5 分钟 beat；sync_pools_minute 自带时间盒 280s + SyncLock 防重叠 + sync_log 可观测。
    stk_mins 限速 1 次/分钟（Valkey 全局闸门），首轮全量可能跨多轮完成（幂等续补）。
    """
    from src.data_sync.pool_minute import sync_pools_minute
    return sync_pools_minute()


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
        notify("critical", "system", "磁盘告警", "\n".join(issues), code="disk.warning")

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

    # P1 修复（2026-08-20 双盲审计 A2）：cron 全链统一北京时区——原 now=UTC 而 last_sync_ts
    # 是 timestamptz（psycopg 返回 UTC aware），croniter 拿表面值算 → "工作日 16:30" 实际
    # 北京次日 00:30 触发（8 条任务全在凌晨跑，cron 1-5 还按 UTC 星期）。init-seed 的
    # cron 值全部按北京时间书写，此处归一后语义即恢复声明。
    TZ_CN = timezone(timedelta(hours=8))
    now = datetime.now(TZ_CN)

    for sid, schedule, enabled, last_status, last_sync_date, last_sync_ts, trade_day_filter in configs:
        if last_status == "running":
            # P1 修复（2026-08-20 双盲审计 A2）：僵尸 running 复位——OOM/SIGKILL 后无代码
            # 复位（_mark_running(False) 只在进程活着时执行）→ beat 永久 skip 该同步。
            # 判定：last_sync_ts 超 2h 未更新（最长任务 sync_all ~70min 留余量）=进程已死。
            _stale = False
            if last_sync_ts:
                try:
                    _age = (now - last_sync_ts.astimezone(TZ_CN)).total_seconds()
                    _stale = _age > 7200
                except Exception:
                    _stale = False
            if _stale:
                try:
                    from src.data_platform.db import get_conn as _gc
                    with _gc() as _c:
                        _c.execute("UPDATE sync_config SET last_status='idle' WHERE id=%s", (sid,))
                        _c.commit()
                    skipped.append(f"{sid}(僵尸running已复位)")
                    logger.warning("同步 %s 僵尸 running（%.0f 分钟未更新）已复位 idle", sid,
                                   (now - last_sync_ts.astimezone(TZ_CN)).total_seconds() / 60)
                except Exception:
                    skipped.append(f"{sid}(运行中)")
            else:
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

        # cron 解析：从上次同步时间算下次到点（P1：base 也归一北京时区，与 now 同基准）
        base = last_sync_ts.astimezone(TZ_CN) if last_sync_ts else (now - timedelta(days=7))
        # F-51（2026-09-03）：游标在未来钳制——last_sync_ts 被时钟回拨/手工改大时，croniter
        # 算出的 next_run 恒在未来 → 该同步永久静默停摆。钳回 now（下次到点即触发）。
        if base > now:
            base = now
        base = base.replace(tzinfo=None) if hasattr(base, "tzinfo") else base   # croniter 用 naive 本地时
        try:
            cron = croniter(schedule, base)
            next_run = cron.get_next(datetime)
        except Exception:
            skipped.append(f"{sid}(cron无效:{schedule})")
            continue

        if next_run > now.replace(tzinfo=None):   # 同基准 naive 比较（P1 时区归一）
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


# 第一档全局同步的 soft_time_limit 覆盖映射（U-3：全市场 batch 拉取带限速必超 300s 默认值）
_TIER1_TIME_LIMITS = {
    "moneyflow_sync": 600, "margin_detail_sync": 600, "cyq_perf_sync": 600,
    "top_list_sync": 600, "block_trade_sync": 300,
}


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
    # R-S4：任务级因子 lazy 重载——celery 启动早于 PG 时 import 期加载静默失败，
    # 且 Web 改因子只更新 web 进程注册表；每次回测任务头重读 DB（幂等，代价一次 SELECT）
    try:
        from src.strategy_framework.factor import load_factors_from_db
        load_factors_from_db()
    except Exception:
        pass

    start = params.get("start", (date.today() - timedelta(days=365)).isoformat())
    end = params.get("end", date.today().isoformat())
    bars_df = get_bars(symbol, "1D", start, end)
    bars = bars_df.to_dict("records") if not bars_df.empty else []

    # 合并参数（链条打磨#14）：parameter_defs 默认值 → 策略级 params → per-symbol 覆盖。
    # 此前不合并默认值——同一策略缺省参数下回测与实盘（live_task 走 build_default_params）行为不同
    strategy_params = json.loads(sc[2]) if sc[2] else {}
    from src.strategy_framework.strategy import build_default_params
    defs = strategy_params.get("parameter_defs") or []
    merged_params = {**build_default_params(defs), **strategy_params, **per_symbol}
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
        # 链条打磨#15：预检/防未来失败（engine 返回 metrics.error 而非抛异常）→ 置 failed
        # 此前照常 status='done' + 存全 0 指标——预检形同虚设
        _pc_err = (result.metrics or {}).get("error")
        if _pc_err:
            _detail = json.dumps({"error": _pc_err,
                                  "issues": (result.metrics or {}).get("issues") or (result.metrics or {}).get("details")},
                                 ensure_ascii=False)
            with get_conn() as conn:
                conn.execute(
                    "UPDATE backtest_symbols SET status='failed', result=%s WHERE run_id=%s AND symbol=%s",
                    (_detail, run_id, symbol))
                conn.commit()
            r.set(pub_key + ":error", _pc_err, ex=3600)
            return {"status": "failed", "symbol": symbol, "error": _pc_err}
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
                "SELECT count(*) FROM backtest_symbols WHERE run_id=%s AND status NOT IN ('done','failed','error')",
                (run_id,))
            pending = cur.fetchone()[0]
            if pending == 0:
                # R-S5：全败 run 置 failed（全 done 才 done）——防零成功回测过 F-44 验证门
                cur2 = conn.execute(
                    "SELECT count(*) FROM backtest_symbols WHERE run_id=%s AND status='done'", (run_id,))
                ok_cnt = cur2.fetchone()[0]
                run_status = 'done' if ok_cnt > 0 else 'failed'
                conn.execute("UPDATE backtest_runs SET status=%s, finished_at=now() WHERE id=%s",
                             (run_status, run_id))
                if run_status == 'done':
                    write_summary_metrics(conn, run_id)   # wd-20 §1.3：成绩单写入方
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


def write_summary_metrics(conn, run_id: int) -> None:
    """wd-20 §1.3：run 终态时聚合 done 符号成绩单（单点写入方——此前 summary_metrics 无写入方，
    前端成绩单恒空）。键名：total_return_pct/max_drawdown_pct/sharpe/win_rate/trade_count。"""
    import json   # 该文件惯例：函数内导入（模块级无 json——原 NameError 盲审复验抓出）
    cur = conn.execute(
        "SELECT result FROM backtest_symbols WHERE run_id=%s AND status='done'", (run_id,))
    rows = [json.loads(r[0]) for r in cur.fetchall() if r[0]]
    if not rows:
        return

    def _avg(key):
        vals = [float(r.get(key) or 0) for r in rows]
        return round(sum(vals) / len(vals), 4) if vals else None

    conn.execute(
        "UPDATE backtest_runs SET summary_metrics=%s WHERE id=%s",
        (json.dumps({
            "total_return_pct": _avg("total_return_pct"),
            "max_drawdown_pct": _avg("max_drawdown_pct"),
            "sharpe": _avg("sharpe_ratio"),
            "win_rate": _avg("win_rate"),
            "trade_count": int(sum(int(r.get("total_trades") or 0) for r in rows)),
        }), run_id))


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
        from src.llm_gateway.budget import check_budget_alerts
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
        # DB 优化（2026-08-21 盘点重灾 #1）：原"SELECT 开事务→事务内 pro.stock_basic() 网络拉取→
        # 逐行 upsert 5400 行"——锁链事件同族（事务跨网络+逐行）。改：先无事务拉取，
        # 再 executemany 批量落库（网络抖动不再持锁；5400 次往返→1 次）。
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name,industry")
        if df is None or df.empty:
            return {"status": "ok", "synced": 0}
        rows = [(r["ts_code"], r.get("name", "") or "", r.get("industry", "") or "")
                for _, r in df.iterrows()]
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO static_symbols (ts_code,name,industry,list_status,delisted) VALUES (%s,%s,%s,'L',false) "
                    "ON CONFLICT (ts_code) DO UPDATE SET name=EXCLUDED.name,industry=EXCLUDED.industry,"
                    "list_status='L',delisted=false,updated_at=now()",
                    rows)
            conn.commit()
        synced = len(rows)
    except Exception as e:
        logger.warning(f"static_list_sync 失败: {e}")
        return {"status": "error", "reason": str(e)[:100]}
    return {"status": "ok", "synced": synced}

@app.task(name="src.scheduler.tasks.broker_health_check")
def broker_health_check():
    """#37 通道用量监控：检查各 broker 连通性，异常告警。

    P1 修复（2026-08-20 双盲审计）：原 cls() 裸构造无凭证必 test_connection=False——
    每 6h 必报"全通道离线"假告警（告警疲劳）。改走 get_broker()（Broker DB 真凭证），
    未配置的通道报 skipped 不告警。
    """
    from src.strategy_framework.broker import get_broker
    from src.alert_notify import notify
    results = {}
    for provider in ("xtp", "binance", "okx"):
        try:
            broker = get_broker(provider)
            if broker is None:
                results[provider] = {"status": "skipped", "msg": "未配置凭证"}
                continue
            ok = broker.test_connection()
            results[provider] = {"status": "ok" if ok else "error"}
        except Exception as e:
            results[provider] = {"status": "error", "msg": str(e)[:100]}
    errors = [k for k, v in results.items() if v["status"] == "error"]
    if errors:
        notify("warn", "system", "通道连通异常", f"离线: {errors}", code="health.channel-down")
    return {"status": "ok" if not errors else "issues", "results": results}





@app.task(name="src.scheduler.tasks.email_outbox_sweep")
def email_outbox_sweep():
    """发件箱扫描：重发到期待发邮件（指数退避由 next_attempt_at 控制，beat 每分钟调）。"""
    from src.email_service import sweep
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


# --- SA4：Failed 实盘单元 reconciler（CrashLoopBackOff，2026-08-23）---

SA4_BACKOFF_BASE = 300   # 首次自动拉起后退避基数（秒）
SA4_BACKOFF_CAP = 3600   # 退避封顶 1h
SA4_STABLE_SECS = 600    # 单元稳定 active 超此时长清退避计数（短暂失败不累积惩罚）
SA4_KEY_PREFIX = "quant:sa4:backoff:"
# 批5 L3 扩面（2026-08-27，docs/任务/批5-L3扩面与polkit配套.md）：
# md-hub 常开语义 + 三重熔断键（D1）；strategy@* 以 is-enabled 显式意图为判定源（D2）
SA4_HUB_UNIT = "quant-md-hub@quant.service"      # hub 期望表条目（系统单例数据面，常开）
SA4_HUB_LEASE_KEY = "hub:lease"                  # 租约 fencing 键（在场=对端实例持有 -> 让位）
SA4_HUB_MAINT_KEY = "quant:maintenance:md-hub"   # 维护标记（人工停 hub 前打，默认 TTL 4h 防遗忘）
SA4_ALERT_TTL = 3600                             # 维护/78 告警去重窗（防 300s 周期刷屏）


def _sa4_systemctl(*args):
    """systemctl 调用（quant 用户经 polkit）；异常/超时返回 None（D-F5：采集失败≠健康）。"""
    import subprocess
    try:
        return subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=10)
    except Exception as e:
        logger.warning("sa4: systemctl %s 失败: %s", " ".join(args), e)
        return None


def _sa4_units(state: str) -> list[str]:
    """按状态列受管单元名（批5 三源扩面：live-task + md-hub + strategy，含 .service 后缀）。

    一次 list-units 传三模式（systemctl 支持多 pattern，省两次调用）；采集失败返回空（D-F5）。
    """
    r = _sa4_systemctl("list-units", "quant-live-task@*", "quant-md-hub@*", "quant-strategy@*",
                       "--state", state, "--no-legend", "--plain", "--no-pager")
    if r is None or r.returncode != 0:
        return []
    return [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]


def _sa4_strategy_unit_files() -> list[str]:
    """列 quant-strategy@* 单元文件名（D2 v2 候选集；是否期望在跑由 is-enabled 显式意图决定）。"""
    r = _sa4_systemctl("list-unit-files", "quant-strategy@*", "--no-legend", "--no-pager")
    if r is None or r.returncode != 0:
        return []
    return [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]


def _desired_units(conn) -> list[tuple[str, str]]:
    """声明式期望表（批5 目标 2）：三处真相源归一为 [(unit, source)]，L3 调和循环统一消费。

    - live_task running -> quant-live-task@{tid}（source=live_task，DB 行）
    - quant-strategy@* -> systemctl is-enabled=enabled 且无 live_task 关联（source=strategy）。
      废架构兼容单元：**enabled DB 行不作拉起依据**（D2 v2——镜像实锤 2-3 行 enabled 无
      live_task 关联，按 DB 拉会部署首周期即拉废 runner）；仅显式 enable 过且无关联才期望在跑，
      is-enabled 与 live_task 并存时排除防双拉（v2.1 去重护栏）
    - md-hub -> 常开（source=builtin，系统单例数据面无 DB 行，永远该在跑）
    """
    cur = conn.execute("SELECT id FROM live_task WHERE status='running'")
    desired = [(f"quant-live-task@{row[0]}.service", "live_task") for row in cur.fetchall()]
    cur = conn.execute(
        "SELECT DISTINCT strategy_id FROM live_task WHERE strategy_id IS NOT NULL")
    linked_sids = {str(row[0]) for row in cur.fetchall()}
    for unit in _sa4_strategy_unit_files():
        sid = unit.split("@", 1)[1].rsplit(".service", 1)[0]
        if sid in linked_sids:
            continue  # 该策略已有 live_task 承载（live-task@{tid} 在跑），strategy@{id} 不双拉
        r = _sa4_systemctl("is-enabled", unit)
        if r is not None and r.returncode == 0 and r.stdout.strip() == "enabled":
            desired.append((unit, "strategy"))
    desired.append((SA4_HUB_UNIT, "builtin"))
    return desired


def _sa4_exec_status(unit: str):
    """读单元 ExecMainStatus（最后退出码；信号死=信号号）。采集失败返回 None（按崩溃处理）。"""
    r = _sa4_systemctl("show", unit, "--property=ExecMainStatus", "--value")
    if r is None or r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except (ValueError, AttributeError):
        return None


def _sa4_hub_guards(r):
    """md-hub 拉起前置熔断（D1 三重的前两重；第三重退避与 L1 共键，在调用方）。

    返回 (ok, reason)：
    - Valkey 不可达/键操作异常 -> fail-closed 跳过（分区期盲拉第二实例会短暂破坏 fencing）
    - 租约键在场 -> 让位跳过（对端实例持有，正常运维态不告警）
    - 维护标记在场 -> 跳过 + 告警（人工维护窗；标记自带 TTL 4h 防裸奔遗忘）
    """
    if r is None:
        return False, "valkey-down"
    try:
        if r.exists(SA4_HUB_LEASE_KEY):
            return False, "lease-held"
        if r.exists(SA4_HUB_MAINT_KEY):
            return False, "maintenance"
    except Exception as e:
        logger.warning("sa4: hub 熔断键查询异常（fail-closed 跳过）: %s", e)
        return False, "valkey-error"
    return True, ""


def _sa4_alert_once(r, dedup_key: str, title: str, body: str, code: str | None = None):
    """去重告警（Valkey 键 TTL 内只发一次）——维护标记/78 配置错防 300s 周期刷屏。

    r 不可用或键操作失败时退化为本周期直发一次（由 beat 周期天然限频）。
    """
    try:
        from src.alert_notify import notify
        if r is not None:
            try:
                if r.set(dedup_key, "1", nx=True, ex=SA4_ALERT_TTL) is None:
                    return  # 去重窗内已发过
            except Exception:
                pass
        notify("warn", "system", title, body, code=code)
    except Exception:
        pass


def _sa4_backoff_delay(attempts: int) -> float:
    """第 attempts 次自动拉起后、下次允许拉起前须等待的时长（指数退避封顶）。"""
    if attempts <= 0:
        return 0.0
    return min(SA4_BACKOFF_BASE * (2 ** (attempts - 1)), SA4_BACKOFF_CAP)


@app.task(name="src.scheduler.tasks.sa4_reconciler")
def sa4_reconciler():
    """SA4 reconciler：L1 Failed 恢复（live-task）+ L3 期望表调和（三源，批5 扩面）。

    - L1（现状逻辑零改动，仅扫描面随三源扩）：Failed live-task 单元，live_task 已停/已删只清
      状态不拉起；退避计数 Valkey（quant:sa4:backoff:{unit}，TTL 1 天）300s*2^(n-1) 封顶 1h
    - L3（批5）：_desired_units 三源归一（live_task running / strategy is-enabled 且无关联 /
      hub 常开）-> systemd 实际状态调和。覆盖 active 缺失漂移 + **failed 态**（ExecMainStatus
      区分：78=配置错跳过告警人工，其他=崩溃 reset-failed+start 走熔断，D1 v2 P0-1）；
      md-hub 拉起前三重熔断：租约（Valkey 不可达 fail-closed）+ 维护标记 + 退避共键
    - PG 不可达：本轮整体跳过（fail-safe--依赖未恢复不盲拉，runner 自身有 systemd Restart 兜底）
    - 单元稳定 active 超 10min 清退避计数（批5 随扫描面泛化到 hub/strategy）
    """
    import os
    import time as _time
    import redis as _redis

    failed = _sa4_units("failed")
    active = _sa4_units("active")
    result = {"failed": len(failed), "restarted": [], "reset_only": [], "skipped": {}}

    # fail-safe：PG 不可达时 runner 的探活也过不了，盲拉只添乱
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
    except Exception as e:
        return {"status": "skipped", "reason": f"PG 不可达: {e}"}

    r = None
    try:
        r = _redis.Redis.from_url(
            os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
        r.ping()
    except Exception as e:
        logger.warning("sa4: Valkey 不可达（退避计数不可用，按首档拉起）: %s", e)

    now = _time.time()
    # 稳定 active -> 清退避计数（下次失败从 300s 重新起算；批5 随扫描面泛化到 hub/strategy）
    if r is not None:
        for unit in active:
            try:
                key = SA4_KEY_PREFIX + unit
                data = r.hgetall(key)
                if data and now - float(data.get("ts", 0)) >= SA4_STABLE_SECS:
                    r.delete(key)
            except Exception:
                pass

    from src.alert_notify import notify
    for unit in failed:
        # 职责边界（v2.1）：L1 只处理 live-task Failed（退避/清零/已停检查现状不动）；
        # md-hub/strategy 的 failed 归下方 L3 段（三重熔断 + ExecMainStatus 区分）
        if not unit.startswith("quant-live-task@"):
            continue
        # 用户意图校验：live_task 已停/已删 -> 只清状态不拉起
        tid = unit.split("@", 1)[1].rsplit(".service", 1)[0]
        try:
            with get_conn() as conn:
                cur = conn.execute("SELECT status FROM live_task WHERE id=%s", (tid,))
                row = cur.fetchone()
        except Exception as e:
            result["skipped"][unit] = f"查 live_task 失败: {e}"
            continue
        if row is None or row[0] != "running":
            _sa4_systemctl("reset-failed", unit)
            result["reset_only"].append(unit)
            logger.info("sa4: %s live_task=%s，只清 Failed 状态不拉起", unit, row[0] if row else "已删")
            continue
        # 退避窗口
        key = SA4_KEY_PREFIX + unit
        data = r.hgetall(key) if r is not None else {}
        attempts = int(data.get("attempts", 0))
        if attempts and now - float(data.get("ts", 0)) < _sa4_backoff_delay(attempts):
            result["skipped"][unit] = "退避窗口内"
            continue
        _sa4_systemctl("reset-failed", unit)
        sr = _sa4_systemctl("start", unit)
        if sr is None or sr.returncode != 0:
            result["skipped"][unit] = f"start 失败: {(sr.stderr or '').strip()[:100] if sr else 'timeout'}"
            continue
        if r is not None:
            r.hset(key, mapping={"attempts": attempts + 1, "ts": _time.time()})
            r.expire(key, 86400)
        result["restarted"].append(unit)
        logger.warning("sa4: %s 自动拉起（第 %d 次，下次退避 %.0fs）",
                       unit, attempts + 1, _sa4_backoff_delay(attempts + 1))
        try:
            notify("warn", "system", f"SA4 自动重启实盘单元: {unit}",
                   f"第 {attempts + 1} 次自动拉起；若再失败将退避 {_sa4_backoff_delay(attempts + 1):.0f}s。",
                   code="sa4.restart")
        except Exception:
            pass
    # --- L3 意图调和（2026-08-24 韧性分层模型；批5 扩面三源）：期望表 -> systemd 实际状态 ---
    # 任务 8 躺 2.5 天实锤（systemctl stop 后 DB 残留 running 无人拉起）；hub 停 2.5 天同类
    # 事故（2026-08-25 SEGV 后 failed 15 分钟无人拉起）证明覆盖面必须从 live-task 扩至三源。
    # 期望表 _desired_units（三处真相源归一）：live_task running / strategy is-enabled / hub 常开。
    # 退避计数与 L1 的 Failed 恢复共用（quant:sa4:backoff:{unit}，同键幂等防双拉：
    # 先到者写计数，后到者在退避窗内跳过）。
    try:
        with get_conn() as conn:
            desired = _desired_units(conn)
        # 复用本轮已采集的 active/failed 列表（不重复 systemctl 调用）
        actives = set(active)
        faileds = set(failed)
        for unit, source in desired:
            if unit in actives:
                continue
            need_reset = False
            if unit in faileds:
                if source == "live_task":
                    continue  # L1 段已处理（v2.1 职责边界）
                # md-hub/strategy failed：ExecMainStatus 区分（D1 v2 P0-1——failed 黑洞根修）
                if _sa4_exec_status(unit) == 78:
                    # EX_CONFIG 永久配置错：自动拉起无意义，跳过 + 告警人工（去重窗内一次）
                    result.setdefault("l3_config_failed", []).append(unit)
                    _sa4_alert_once(r, f"quant:sa4:alert78:{unit}",
                                    f"L3 跳过配置错单元: {unit}",
                                    "ExecMainStatus=78（EX_CONFIG）自动拉起无意义，"
                                    "请人工修复后 systemctl reset-failed + start。",
                                    code="unit.config-err")
                    logger.warning("L3: %s ExecMainStatus=78 配置错，跳过拉起待人工", unit)
                    continue
                need_reset = True  # 崩溃 failed（含 StartLimit 打穿）-> 拉起前先清 failed 态
            # md-hub 前置熔断（D1 前两重）：租约（Valkey 不可达 fail-closed）/维护标记
            if source == "builtin":
                ok, reason = _sa4_hub_guards(r)
                if not ok:
                    if reason == "maintenance":
                        _sa4_alert_once(r, f"quant:sa4:alert-maint:{unit}",
                                        f"L3 维护窗跳过拉起: {unit}",
                                        f"维护标记 {SA4_HUB_MAINT_KEY} 在场，hub 不自动拉起；"
                                        "维护完成请删标记（标记 TTL 4h 自动过期）。",
                                        code="hub.maint")
                    elif reason == "valkey-down":
                        # fail-closed 但要让植物人可见：直发一次（r 不可用无法跨周期去重，
                        # 由 beat 300s 周期限频）
                        logger.warning("L3: %s Valkey 不可达，fail-closed 跳过拉起", unit)
                        try:
                            notify("warn", "system", f"L3 fail-closed 跳过拉起: {unit}",
                                   "Valkey 不可达无法验 hub 租约，本轮不拉起"
                                   "（防盲拉第二实例短暂破坏 fencing）。",
                                   code="l3.skip-valkey")
                        except Exception:
                            pass
                    result.setdefault("l3_guards", {})[unit] = reason
                    continue
            # 退避窗口（与 L1 共键；r 不可用按首档——hub 已被上方 fail-closed 拦下，
            # 此处仅 live-task/strategy 走到）
            key = SA4_KEY_PREFIX + unit
            data = r.hgetall(key) if r else {}
            attempts = int(data.get("attempts", 0))
            if attempts and _time.time() - float(data.get("ts", 0)) < _sa4_backoff_delay(attempts):
                result.setdefault("l3_skipped", []).append(unit)
                continue
            if need_reset:
                _sa4_systemctl("reset-failed", unit)
            sr = _sa4_systemctl("start", unit)
            if sr is None or sr.returncode != 0:
                # P2(G4 ④a): stderr 采集+告警——原版静默丢弃,持续失败会 300s 重试无人知
                stderr = (sr.stderr or '').strip()[:100] if sr else 'timeout'
                result.setdefault("l3_failed", []).append(f"{unit}: {stderr}")
                try:
                    notify("critical", "system", f"L3 拉起失败: {unit}",
                           f"systemctl start 非零退出。stderr: {stderr}；"
                           "L3 将按 beat 周期重试，持续失败请手动 journalctl -u {unit} 定位。",
                           code="l3.failed")
                except Exception:
                    pass
                continue
            if r is not None:
                r.hset(key, mapping={"attempts": attempts + 1, "ts": _time.time()})
                r.expire(key, 86400)
            result.setdefault("l3_restarted", []).append(unit)
            logger.warning("L3 意图调和：期望源=%s 在但单元缺失/崩溃 failed，拉起 %s", source, unit)
            try:
                notify("warn", "system", f"L3 拉起单元: {unit}",
                       f"期望源={source}，systemd 无实例或崩溃 failed，已自动拉起。"
                       "若预期停用：live-task 先在 Web 停止任务；hub 打维护标记或 systemctl mask。",
                       code="l3.pull")
            except Exception:
                pass
    except Exception as e:
        logger.warning("L3 意图调和失败: %s", e)

    return result
