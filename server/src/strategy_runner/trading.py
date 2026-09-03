"""交易域共享件（批 4a 提取，2026-08-27）：direct（main.py）与 hub worker 单源化。

九单元：write_trade_log / snapshot_cycle / halt_edge_cancel / recalc_hook / stop_due /
reconcile_orders / frozen_allows / buy_ok_check / _flush_positions。

设计（docs/任务/批4-worker迁移与trading解耦.md v2.1）：
- 依赖注入：函数收 adapter/缓存参数，零模块级可变状态——账户基线缓存改调用方持有 dict
  （每进程恰一个快照调用方，与原模块级缓存"基线不随运行漂移"语义等价）；
- vnpy 全部函数内 lazy import（本模块须能在无 vnpy 环境被测试加载，与 main.py 同策略）；
- 知情差异五条（v2.1 裁定的双模式统一，语义零漂移的唯一例外）：
  ① reconcile_orders=runner 超集（在场委托+成交补录+WAL 残留——worker 由只告警升级，知情接受）
  ② snapshot_cycle=direct 形态（含 available_cash、单事务——worker 落库多一列，无消费者受扰）
  ③ 停止检查节奏各自保持（worker 5s / direct 60s，节奏在调用侧——统一即违反 direct 冻结）
  ④ 熔断/重算告警文案统一 direct 版
  ⑤ write_trade_log 统一 RETURNING 版（worker 侧同步获得"成交入库"观测日志）
"""
import logging

logger = logging.getLogger("strategy_runner.trading")

FROZEN_STALE_BAR_S = 300     # 交易时段无新 bar 冻结（评审 S6 worker 侧防线；buy_ok 门限）


def _alert(title: str, body: str = "", code: str | None = None) -> None:
    """交易域告警：never-raise（safe_notify），绝不影响交易主流程（与 main/hub_worker 同款）。"""
    from src.alert_notify.notify import safe_notify
    safe_notify("critical", title, body, code=code)


def buy_ok_check(frozen: dict, stats: dict, hub_alive: bool, now: float,
                 in_session: bool = True) -> bool:
    """send_order 时刻事实检查（S6 修订，纯函数供测试）：BUY 需 交易时段 + bar 新鲜（<300s）+ hub 心跳。

    交易时段门（盲审 C-F2 2026-08-18）：XTP 测试平台夜间回放白昼行情——bar 流动且锚新鲜，
    worker 重启后 max_ts 失忆不 R-DL1 去重，回放 bar 会真实驱动下单（重复消费旧数据）。
    时段外拒 BUY 属业务正确（A 股连续竞价时段外委托不可成交），误拒方向安全；SELL 不受限（R-AV2）。
    """
    if not in_session:
        return False
    if frozen.get("sticky"):
        return False
    fresh = stats.get("last_bar_wall", 0) and (now - stats["last_bar_wall"] < FROZEN_STALE_BAR_S)
    return bool(fresh) and hub_alive


def frozen_allows(action: str, frozen: dict) -> bool:
    """sticky 冻结（数据污染事实：untrusted bar / 流 gap）期 BUY 拒 / SELL 放（R-AV2，与 SB F-31 同哲学）。

    S6 修订（2026-08-18）：只判 sticky——动态新鲜度（hub 心跳/bar 停更）不再进 frozen["now"]
    参与下单判定，改由 send_order 时刻的 buy_ok 事实检查完成（ctx 注入）。C2 网关与测试共用同一判定。
    """
    if not frozen.get("sticky"):
        return True
    return str(action).upper() == "SELL"


def write_trade_log(d, adapter, sid: str, symbol: str) -> None:
    """TradeData → trade_log（SC1，EVENT_TRADE 与启动/重连对账共用）。

    幂等：trade_ref 唯一索引 + ON CONFLICT DO NOTHING。RETURNING 版（4a 统一——worker 侧
    同步获得成交入库观测日志，知情差异⑤）。
    """
    try:
        from vnpy.trader.constant import Direction
        from src.data_platform.db import get_conn
        action = "BUY" if d.direction == Direction.LONG else "SELL"
        vt = getattr(d, "vt_orderid", "")
        with adapter._lock:
            cid = adapter._vt2cid.get(vt)
        order_db_id, strategy_of = None, sid
        with get_conn() as conn:
            if cid:
                cur = conn.execute(
                    "SELECT id, strategy_id FROM order_log WHERE client_order_id=%s ORDER BY id DESC LIMIT 1",
                    (cid,))
                row = cur.fetchone()
                if row:
                    order_db_id, strategy_of = row[0], row[1] or sid
            cur = conn.execute(
                "INSERT INTO trade_log (ts, strategy_id, order_id, symbol, action, volume, price, trade_ref) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (trade_ref) DO NOTHING RETURNING id",
                (getattr(d, "datetime", None), strategy_of, order_db_id, getattr(d, "symbol", symbol),
                 action, float(getattr(d, "volume", 0) or 0), float(getattr(d, "price", 0) or 0),
                 getattr(d, "vt_tradeid", None) or None))
            if cur.fetchone():
                logger.info("成交入库: %s %s %s@%s (order_db=%s)", getattr(d, "symbol", symbol),
                            action, getattr(d, "volume", 0), getattr(d, "price", 0), order_db_id)
            conn.commit()
    except Exception as e:
        logger.warning("trade_log 写入失败: %s", e)


def _flush_positions(adapter, account_id, task_id) -> None:
    """ST2 持仓真相源写批（N 审 v2）：60s 循环取 query_position() 返回值，单事务覆盖式写。

    - position_snapshot = 当前状态表：DELETE 该账户旧行 + INSERT 当前批（N-F1：清仓 0 行回报
      也能表示空仓；行数常数无需保留期）
    - position_refresh 心跳同事务 upsert（rows=本批行数）——区分"空仓"与"停更"（N-S5）
    - account_id 为真相维度（N-S4：query_position 回报=全账户仓位，与任务标的无关）
    - 失败仅日志，不阻断主循环
    """
    try:
        from src.data_platform.db import get_conn
        acct = str(account_id) if account_id else "default"
        positions = adapter.query_position() or []
        with get_conn() as conn:
            conn.execute("DELETE FROM position_snapshot WHERE account_id=%s", (acct,))
            if positions:
                # O-F1：池化连接无 executemany（F 审同款坑）——走 cursor；
                # O-S8：ON CONFLICT 幂等——两任务同账户同拍写时 last-write-wins 而非互崩
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO position_snapshot (account_id, symbol, direction, volume, frozen, "
                        "cost_price, pnl, yd_volume, task_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (account_id, symbol, direction) DO UPDATE SET volume=EXCLUDED.volume, "
                        "frozen=EXCLUDED.frozen, cost_price=EXCLUDED.cost_price, pnl=EXCLUDED.pnl, "
                        "yd_volume=EXCLUDED.yd_volume, task_id=EXCLUDED.task_id",
                        [(acct, p.symbol, getattr(p, "direction", "long"), int(p.volume),
                          int(getattr(p, "frozen", 0) or 0), float(p.avg_price or 0),
                          float(getattr(p, "pnl", 0) or 0), int(getattr(p, "yd_volume", 0) or 0),
                          str(task_id) if task_id is not None else None) for p in positions])
            conn.execute(
                "INSERT INTO position_refresh (account_id, ts, rows, task_id) VALUES (%s, now(), %s, %s) "
                "ON CONFLICT (account_id) DO UPDATE SET ts=now(), rows=%s, task_id=%s",
                (acct, len(positions), str(task_id) if task_id is not None else None,
                 len(positions), str(task_id) if task_id is not None else None))
            conn.commit()
    except Exception as e:
        logger.warning("ST2 持仓快照写批失败（不阻断）: %s", e)


def _account_baseline_capital(total: float, cache: dict) -> float:
    """账户基线净值（#10 口径修正 2026-08-22）。

    account_snapshot.initial_capital 原写 live_task 配置资金（策略级，默认 100 万），
    而 total_value 是账户级真值（如测试账户 10 亿）--total_pnl 虚增 9.99 亿、风控回撤
    分母错配。改：基线=该账户首条快照 total_value（跟踪起点净值）；无历史（首次跟踪）
    以当前查询值为基线。cache 由调用方持有（每进程恰一个快照调用方 → 基线不随运行漂移，
    与原模块级缓存语义等价；4a 消除模块级可变状态）。
    """
    if cache.get("baseline") is None:
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute("SELECT total_value FROM account_snapshot ORDER BY ts ASC LIMIT 1")
                row = cur.fetchone()
            cache["baseline"] = float(row[0]) if row and row[0] else total
        except Exception as e:
            logger.warning("读账户基线净值失败（以当前值为基线）: %s", e)
            cache["baseline"] = total
    return cache["baseline"]


def snapshot_cycle(adapter, account_id, tid, baseline_cache: dict) -> None:
    """定期账户快照 + 持仓真相批（#6 每 60s / ST2；direct 形态——4a 双模式统一，知情差异②）。

    - SB1（F-34）：query_account 无结果（TD 断线/查询超时）绝不写假值——旧逻辑把
      initial_capital 当总资产写入，恰好把风控回撤"归零回正"；快照与持仓批同守卫双跳过
      （O-F3：断线返回 [] 会把真实持仓写成"新鲜空仓"=假真相，恰是 N-S5 要防的）。
    - 单事务：当日基准查询 + INSERT account_snapshot（含 available_cash——DB 优化批
      2026-08-21 审计 F4.1：vnpy AccountData 无 available 字段，XTP 现金账户 balance-frozen
      近似，PERCENT/ALL_IN sizing 真口径）；P3-10 daily_pnl=今日首次快照基准的偏差。
    - ST2 同拍写持仓批：_flush_positions 自持连接（N-v2：取返回值单事务覆盖，非
      EVENT_POSITION handler）。
    """
    try:
        from src.data_platform.db import get_conn
        accounts = adapter.query_account() or []
        if not accounts:
            logger.warning("query_account 无结果（TD 断线？），跳过本轮快照（不写假值）")
        else:
            total = sum(float(getattr(a, "balance", 0)) for a in accounts)
            # DB 优化批（2026-08-21 审计 F4.1）：可用资金（vnpy AccountData 无 available
            # 字段，XTP 现金账户 balance-frozen 近似）——PERCENT/ALL_IN sizing 真口径
            avail = sum(max(0.0, float(getattr(a, "balance", 0)) - float(getattr(a, "frozen", 0) or 0))
                        for a in accounts)
            # P3-10 daily_pnl = 今日首次快照基准的偏差
            import datetime as _dt2
            today_str = _dt2.datetime.now().strftime('%Y-%m-%d')
            with get_conn() as conn:
                cur = conn.execute("SELECT total_value FROM account_snapshot WHERE ts::date=%s ORDER BY ts ASC LIMIT 1", (today_str,))
                first_row = cur.fetchone()
                daily_base = float(first_row[0]) if first_row else total
                daily_pnl = total - daily_base
                conn.execute("INSERT INTO account_snapshot (total_value, daily_pnl, initial_capital, available_cash) VALUES (%s, %s, %s, %s)",
                             (total, daily_pnl, _account_baseline_capital(total, baseline_cache), avail))
                # ST2：同拍写持仓真相批（N-v2：取返回值单事务覆盖，非 EVENT_POSITION handler）
                _flush_positions(adapter, account_id, tid)
                conn.commit()
    except Exception as e:
        logger.warning("写 account_snapshot 失败: %s", e)


def halt_edge_cancel(adapter, halt_state: dict, sid) -> None:
    """SB2 熔断沿：进入熔断的瞬间撤全部在场委托（F-41——熔断只拦新单不撤旧单=熔断期间仍建仓）。

    halt_state={"was": bool} 由调用方持有（沿检测状态跨拍保持）；Valkey 不可达时保持上一
    状态（check_order 侧已保守拒单）。告警文案统一 direct 版（4a 知情差异④）。
    """
    try:
        from src.risk_control.risk import RiskControl
        halted_now = RiskControl.get().is_halted()
    except Exception:
        halted_now = halt_state["was"]  # Valkey 不可达时保持上一状态（check_order 侧已保守拒单）
    if halted_now and not halt_state["was"]:
        logger.critical("检测到熔断，撤销全部在场委托")
        _alert(f"熔断触发，已自动撤销在场委托: {sid}", "check_order 已拒新单；在场委托撤销结果见 journalctl。", code="risk.halt-edge")
        try:
            from vnpy.trader.constant import Status
            working = (Status.SUBMITTING, Status.NOTTRADED, Status.PARTTRADED)
            for od in (adapter.query_orders() or []):
                if getattr(od, "status", None) in working:
                    try:
                        adapter.cancel_order(od.vt_orderid)
                    except Exception as ce:
                        logger.warning("撤单失败 %s: %s", od.vt_orderid, ce)
        except Exception as e:
            logger.error("熔断撤单流程异常: %s", e)
    halt_state["was"] = halted_now


_recalc_seen: str | None = None   # F-55：本 worker 进程最近一次消费的因子重算触发标记值（多 worker 各记，不删全局键）


def recalc_hook(r, rewarm, history) -> None:
    """因子重算触发（#31，data_continuity_check 补采后设标记 -> 重填 history）。

    链条打磨#6：同标记兼作因子热重载钩子（Web 改因子后 runner 不重启即生效）。
    rewarm 由调用方注入（direct：PG 重填 history；worker：PG+流回放重暖机）；
    日志文案统一 direct 版（4a 知情差异④）。
    """
    global _recalc_seen
    try:
        val = r.get("factor:recalc:triggered")
        if val and val != _recalc_seen:
            # F-55（2026-09-03）：多 worker 各记 last_seen、读到新标记才重算且不删全局键——
            # 原 r.delete 使第一个抢到的 worker 删键后，其余 worker 全部错过因子重算。
            _recalc_seen = val
            try:
                from src.strategy_framework.factor import load_factors_from_db
                load_factors_from_db()
            except Exception:
                pass
            rewarm()
            logger.info("因子重算触发：重填 %d 根历史 bar", len(history))
    except Exception as e:
        logger.warning("因子重算触发检查失败: %s", e)


def stop_due(tid, sid) -> bool:
    """P4-3 停止条件检查（tid/sid 双态单源；节奏在调用侧——worker 5s / direct 60s 各自保持，
    4a/v2.1 裁定：统一节奏即违反 direct 冻结）。

    新架构查自己的 live_task.status（stop_live_task 置 stopped）；旧架构查
    strategy_config.enabled。2026-08-17 踩坑：新架构误查旧架构字段，策略未 enable 的
    任务每 60s 自杀重启，history 永远攒不满出不了信号。异常仅告警返回 False（不退出）。
    """
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            if tid is not None:
                cur = conn.execute("SELECT status FROM live_task WHERE id=%s", (tid,))
                r = cur.fetchone()
                return bool(r and r[0] == "stopped")
            cur = conn.execute("SELECT enabled FROM strategy_config WHERE id=%s", (sid,))
            r = cur.fetchone()
            return bool(r and not r[0])
    except Exception as e:
        logger.warning("停止条件检查失败: %s", e)
        return False


def reconcile_orders(adapter, sid, symbol=None) -> None:
    """SC2 启动/重连对账（runner 超集——4a 知情差异①：worker 由只告警在场委托升级为三件套，
    启动与每次 TD 重连沿均变化，知情接受）。

    v1 策略：发现残留委托只告警不自动撤（防误杀人工单）；成交补录靠 trade_ref 幂等。
    """
    try:
        from vnpy.trader.constant import Status
        working = [o for o in (adapter.query_orders() or [])
                   if getattr(o, "status", None) in (Status.SUBMITTING, Status.NOTTRADED, Status.PARTTRADED)]
        if working:
            desc = "; ".join(
                f"{o.symbol} {getattr(o.direction, 'value', '?')} {o.volume}@{o.price}"
                for o in working[:10])
            logger.warning("启动对账：%d 笔在场委托: %s", len(working), desc)
            _alert(f"启动对账发现 {len(working)} 笔在场委托（任务 {sid}）",
                   desc + " —— 疑似上次会话残留，请确认并决定是否人工撤销。", code="reconcile.open-orders")
        trades = adapter.query_trades() or []
        n_new = 0
        for t in trades:
            write_trade_log(t, adapter, sid, symbol)
            n_new += 1
        if trades:
            logger.info("启动对账：补录当日成交 %d 笔（trade_ref 幂等去重）", n_new)
        # 提交中残留（WAL 崩溃窗口证据）：上一会话 submitting 但无对应成交/委托 → 标记
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT id, symbol, action, volume FROM order_log WHERE strategy_id=%s "
                    "AND status='submitting' AND ts::date=current_date", (sid,))
                orphans = cur.fetchall()
            for oid, osym, oact, ovol in orphans:
                logger.warning("WAL 残留 submitting 单 id=%s %s %s %s（上会话崩溃窗口），待人工核对", oid, osym, oact, ovol)
            if orphans:
                _alert(f"WAL 残留 {len(orphans)} 笔 submitting 委托（任务 {sid}）",
                       "上一会话在'记账后、确认前'中断。请对照券商委托列表核对后人工处理。",
                       code="reconcile.wal")
        except Exception as e:
            logger.warning("WAL 残留检查失败: %s", e)
    except Exception as e:
        logger.warning("启动对账失败: %s", e)
