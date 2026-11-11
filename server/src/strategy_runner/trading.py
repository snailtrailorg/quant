"""交易域共享件（批 4a 提取，2026-08-27）：direct（main.py）与 hub worker 单源化。

九单元：write_trade_log / snapshot_cycle / halt_edge_cancel / recalc_hook / stop_due /
reconcile_orders / frozen_allows / buy_ok_check / _flush_positions。

设计（docs/obsolete/任务归档/批4-worker迁移与trading解耦.md v2.1）：
- 依赖注入：函数收 adapter/缓存参数，零模块级可变状态——账户基线缓存改调用方持有 dict
  （每进程恰一个快照调用方，与原模块级缓存"基线不随运行漂移"语义等价）；
  （唯一例外：`_recalc_seen`——F-55 因子重算触发标记，本 worker 进程各记、不删全局键，设计内）
- vnpy 全部函数内 lazy import（本模块须能在无 vnpy 环境被测试加载，与 main.py 同策略）；
- 知情差异五条（v2.1 裁定的双模式统一，语义零漂移的唯一例外）：
  ① reconcile_orders=runner 超集（在场委托+成交补录+WAL 残留——worker 由只告警升级，知情接受）
  ② snapshot_cycle=direct 形态（含 available_cash、单事务——worker 落库多一列，无消费者受扰）
  ③ 停止检查节奏各自保持（worker 5s / direct 60s，节奏在调用侧——统一即违反 direct 冻结）
  ④ 熔断/重算告警文案统一 direct 版
  ⑤ write_trade_log 统一 RETURNING 版（worker 侧同步获得"成交入库"观测日志）
"""
import json
import logging

from src.data_platform.tz import as_utc  # 批 56b 盲审 B：trade_log ts 收口（vnpy datetime naive——pin UTC 后裸写=错 8h）
from src.quant_common.domain import Direction, OrderState, OrderStatus, Trade
from src.strategy_runner.legacy_translate import status_of, to_domain_trade

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
    # 批 171s 件二 P1-B：放行判据单源化——构造最小 order dict 交 risk.is_flat_or_reduce。
    # 签名保留 (action, frozen)：本网关（strategy.place_order C2，main.py:497）只收到方向
    # 字符串，拿不到完整 order；急停平仓单不经此网关（走 check_order，order 自带
    # reason="emergency_close_all"，由 is_flat_or_reduce 在该链放行派生方向的 BUY 平空）。
    # 故此处只传 {"action": action} 不塞 reason——否则 sticky 期普通 BUY 开仓会被误放行。
    from src.risk_control.risk import is_flat_or_reduce
    return is_flat_or_reduce({"action": action})


ORDER_FINAL_STATES = ("all_traded", "cancelled", "rejected")   # 批 68 终态集（SQL 闭包=只进不退主防线）


def _client_id_of(d, adapter, vt: str) -> str:
    """cid 读取：adapter 当前 _vt2cid 优先；adapter 无该属性/None 才用 domain 快照。

    与基线「读进程内存现值」同刻；domain 字段只作 P3 端口兜底（禁快照优先）。
    """
    if adapter is not None and hasattr(adapter, "_vt2cid"):
        lock = getattr(adapter, "_lock", None)
        try:
            if lock is not None:
                with lock:
                    cid = adapter._vt2cid.get(vt, "")
            else:
                cid = adapter._vt2cid.get(vt, "")
            return cid if isinstance(cid, str) else ""
        except Exception:
            return ""
    return getattr(d, "client_id", "") or ""


def write_order_status(d: OrderState, adapter, sid: str, symbol: str) -> None:
    """OrderState → order_log.status 终态回写（批 68；P1b 改读 domain）。

    映射：PARTIAL→partial/ALL_TRADED→all_traded/CANCELLED→cancelled/REJECTED→rejected；
    SUBMITTED/NOT_TRADED 不回写（提交侧已管）。只进不退：SQL 终态集闭包（迟到片段/乱序
    天然挡）；send_failed=提交侧伪终态可被覆写（超时类实达柜台终态迟到=合法纠正）。
    关联键：vt_orderid 两步（非唯一索引——SELECT LIMIT 1 最新再 UPDATE WHERE id）；
    空则 _lock 下 _vt2cid 反查 client_order_id（唯一索引）。never-raise。
    """
    try:
        m = {OrderStatus.PARTIAL: "partial", OrderStatus.ALL_TRADED: "all_traded",
             OrderStatus.CANCELLED: "cancelled", OrderStatus.REJECTED: "rejected"}
        new_status = m.get(getattr(d, "status", None))
        if not new_status:
            return   # SUBMITTED/NOT_TRADED=非推进
        from src.data_platform.db import get_conn
        vt = getattr(d, "vt_orderid", "") or ""
        with get_conn() as conn:
            row = None
            if vt:
                # 两步（P2-2）：vt_orderid 非唯一（0063）——EMT per-client 序列跨任务/跨日碰撞，
                # 直 UPDATE 可击中多行；取最新一行（与 write_trade_log 反查同款）
                cur = conn.execute(
                    "SELECT id FROM order_log WHERE vt_orderid=%s ORDER BY id DESC LIMIT 1", (vt,))
                row = cur.fetchone()
            if not row:
                cid = _client_id_of(d, adapter, vt)
                if cid:
                    cur = conn.execute(
                        "SELECT id FROM order_log WHERE client_order_id=%s ORDER BY id DESC LIMIT 1", (cid,))
                    row = cur.fetchone()
            if not row:
                logger.warning("order_log 终态回写跳过（无关联行）: vt=%s status=%s %s",
                               vt, new_status, getattr(d, "symbol", symbol))
                return
            # 2026-11-09（171n e2e 抓真库语法错）：psycopg 不做元组展开，NOT IN %s
            # 渲染成 NOT IN $3 ⇒ 语法错 ⇒ 终态回写从未成功（never-raise 静默吞）。
            # 数组比较用 <> ALL(%s)（仓内 ANY(%s) 同族写法）。
            cur = conn.execute(
                "UPDATE order_log SET status=%s WHERE id=%s AND status <> ALL(%s)",
                (new_status, row[0], list(ORDER_FINAL_STATES)))
            if cur.rowcount:
                logger.info("order_log 终态: #%s → %s (%s)", row[0], new_status, getattr(d, "symbol", symbol))
            conn.commit()
    except Exception as e:
        logger.warning("order_log 终态回写失败(never-raise): %s", e)


def write_trade_log(d: Trade, adapter, sid: str, symbol: str) -> None:
    """Trade → trade_log（SC1，EVENT_TRADE 与启动/重连对账共用；P1b 改读 domain）。

    幂等：trade_ref 唯一索引 + ON CONFLICT DO NOTHING。RETURNING 版（4a 统一）。
    查询顺序互斥（与基线逐字）：cid 非空只查 cid（查不到不回落 vt）；cid 空才查 vt。
    trade_ref 空值写 NULL（PG 唯一索引对 NULL 不去重；写 "" 会让后续成交被静默丢弃）。
    """
    try:
        if adapter is None:
            # 早到事件（adapter 未绑定）不写 NULL account 行：ON CONFLICT 会把该行永久定格，
            # 之后 reconcile 带正确 account 的补录被吞（P3 端口先订阅后绑定时可达）。
            logger.warning("trade_log 写入跳过（adapter 未绑定）: %s", getattr(d, "tradeid", ""))
            return
        from src.data_platform.db import get_conn
        action = "BUY" if getattr(d, "direction", None) == Direction.LONG else "SELL"
        vt = getattr(d, "orderid", "") or ""
        cid = _client_id_of(d, adapter, vt)
        order_db_id, strategy_of = None, sid
        with get_conn() as conn:
            if cid:
                cur = conn.execute(
                    "SELECT id, strategy_id FROM order_log WHERE client_order_id=%s ORDER BY id DESC LIMIT 1",
                    (cid,))
                row = cur.fetchone()
                if row:
                    order_db_id, strategy_of = row[0], row[1] or sid
            elif vt:
                # F-50：重启后 _vt2cid 进程内存丢失，cid=None——用 vt_orderid 反查（order_log 已存该列）
                cur = conn.execute(
                    "SELECT id, strategy_id FROM order_log WHERE vt_orderid=%s ORDER BY id DESC LIMIT 1",
                    (vt,))
                row = cur.fetchone()
                if row:
                    order_db_id, strategy_of = row[0], row[1] or sid
            cur = conn.execute(
                "INSERT INTO trade_log (ts, strategy_id, order_id, symbol, action, volume, price, trade_ref, account_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (trade_ref) DO NOTHING RETURNING id",
                (as_utc(getattr(d, "datetime", None)) if getattr(d, "datetime", None) is not None else None,
                 strategy_of, order_db_id, getattr(d, "symbol", symbol),
                 action, float(getattr(d, "volume", 0) or 0), float(getattr(d, "price", 0) or 0),
                 getattr(d, "tradeid", None) or None,
                 getattr(adapter, "account_id", None)))
            if cur.fetchone():
                logger.info("成交入库: %s %s %s@%s (order_db=%s)", getattr(d, "symbol", symbol),
                            action, getattr(d, "volume", 0), getattr(d, "price", 0), order_db_id)
            conn.commit()
    except Exception as e:
        logger.warning("trade_log 写入失败: %s", e)


def _flush_positions(adapter, account_id, task_id) -> None:
    """ST2 持仓真相源写批（N 审 v2）：60s 循环取 query_position() 返回值，单事务覆盖式写。

    - position_snapshot = 当前状态表：DELETE 该 account 旧行 + INSERT 当前批（N-F1：清仓 0 行回报
      也能表示空仓；行数常数无需保留期）
    - position_refresh 心跳同事务 upsert（rows=本批行数）——区分"空仓"与"停更"（N-S5）
    - account_id 为真相维度（D2：N-S4 query_position 回报=该 account 全账户仓位，与任务标的无关）
    - 失败仅日志，不阻断主循环
    """
    try:
        from src.data_platform.db import get_conn
        positions = adapter.query_position() or []
        with get_conn() as conn:
            conn.execute("DELETE FROM position_snapshot WHERE account_id=%s", (account_id,))
            if positions:
                # O-F1：池化连接无 executemany（F 审同款坑）——走 cursor；
                # O-S8：ON CONFLICT 幂等——两任务同 account 同拍写时 last-write-wins 而非互崩
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO position_snapshot (account_id, symbol, direction, volume, frozen, "
                        "cost_price, pnl, yd_volume, task_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (account_id, symbol, direction) DO UPDATE SET volume=EXCLUDED.volume, "
                        "frozen=EXCLUDED.frozen, cost_price=EXCLUDED.cost_price, pnl=EXCLUDED.pnl, "
                        "yd_volume=EXCLUDED.yd_volume, task_id=EXCLUDED.task_id",
                        [(account_id, p.symbol, getattr(p, "direction", "direction_long"), int(p.volume),
                          int(getattr(p, "frozen", 0) or 0), float(p.avg_price or 0),
                          float(getattr(p, "pnl", 0) or 0), int(getattr(p, "yd_volume", 0) or 0),
                          str(task_id) if task_id is not None else None) for p in positions])
            conn.execute(
                "INSERT INTO position_refresh (account_id, ts, rows, task_id) VALUES (%s, now(), %s, %s) "
                "ON CONFLICT (account_id) DO UPDATE SET ts=now(), rows=%s, task_id=%s",
                (account_id, len(positions), str(task_id) if task_id is not None else None,
                 len(positions), str(task_id) if task_id is not None else None))
            conn.commit()
    except Exception as e:
        logger.warning("ST2 持仓快照写批失败（不阻断）: %s", e)


def _account_baseline_capital(total: float, cache: dict, account_id) -> float:
    """账户基线净值（#10 口径修正 2026-08-22；D2 起 per-account）。

    account_snapshot.initial_capital 原写 live_task 配置资金（策略级，默认 100 万），
    而 total_value 是账户级真值（如测试账户 10 亿）--total_pnl 虚增 9.99 亿、风控回撤
    分母错配。改：基线=该 account 首条快照 total_value（跟踪起点净值）；无历史（首次跟踪）
    以当前查询值为基线。cache 由调用方持有（每进程恰一个快照调用方 → 基线不随运行漂移，
    与原模块级缓存语义等价；4a 消除模块级可变状态）。
    """
    if cache.get("baseline") is None:
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT total_value FROM account_snapshot WHERE account_id=%s ORDER BY ts ASC LIMIT 1",
                    (account_id,))
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
                cur = conn.execute("SELECT total_value FROM account_snapshot WHERE account_id=%s AND (ts AT TIME ZONE 'Asia/Shanghai')::date=%s ORDER BY ts ASC LIMIT 1", (account_id, today_str))
                first_row = cur.fetchone()
                daily_base = float(first_row[0]) if first_row else total
                daily_pnl = total - daily_base
                conn.execute("INSERT INTO account_snapshot (account_id, total_value, daily_pnl, initial_capital, available_cash) VALUES (%s, %s, %s, %s, %s)",
                             (account_id, total, daily_pnl, _account_baseline_capital(total, baseline_cache, account_id), avail))
                # ST2：同拍写持仓真相批（N-v2：取返回值单事务覆盖，非 EVENT_POSITION handler）
                _flush_positions(adapter, account_id, tid)
                conn.commit()
    except Exception as e:
        logger.warning("写 account_snapshot 失败: %s", e)


def halt_edge_cancel(adapter, halt_state: dict, sid) -> None:
    """SB2 熔断沿：进入熔断的瞬间撤全部在场委托（F-41——熔断只拦新单不撤旧单=熔断期间仍建仓）。

    批 171n：撤单核心已提纯 `_cancel_working_orders`（本函数留沿判定+告警壳——F-41
    行为零漂移）。
    """
    try:
        from src.risk_control.risk import RiskControl
        halted_now = RiskControl.get().is_halted()
    except Exception:
        halted_now = halt_state["was"]  # Valkey 不可达时保持上一状态（check_order 侧已保守拒单）
    if halted_now and not halt_state["was"]:
        logger.critical("检测到熔断，撤销全部在场委托")
        _alert(f"熔断触发，已自动撤销在场委托: {sid}", "check_order 已拒新单；在场委托撤销结果见 journalctl。", code="risk.halt-edge")
        _cancel_working_orders(adapter)
    halt_state["was"] = halted_now


def _cancel_working_orders(adapter) -> dict:
    """批 171n：撤全部在场委托（F-41 提纯+一键撤单共用核心；P1b 改读 domain）。

    返回 {"requested": N, "failed": M, "untranslatable": U}——requested/failed 语义不变
    （请求已受理/撤单异常）；untranslatable=状态或 vt_orderid 不可读而跳过的单（可观测，
    不冒充撤单成功）。在场判定只读 status+vt_orderid 两字段（与基线同，缺字段不漏撤）。
    """
    out = {"requested": 0, "failed": 0, "untranslatable": 0}
    try:
        working = {OrderStatus.SUBMITTED, OrderStatus.NOT_TRADED, OrderStatus.PARTIAL}
        for od in (adapter.query_orders() or []):
            try:
                st = status_of(od)
            except Exception as e:
                out["untranslatable"] += 1
                logger.warning("撤单状态翻译失败（跳过）: %s", e)
                continue
            if st not in working:
                continue
            vt = getattr(od, "vt_orderid", "") or ""
            if not vt:
                out["untranslatable"] += 1
                logger.warning("在场委托缺 vt_orderid，跳过撤单")
                continue
            out["requested"] += 1
            try:
                adapter.cancel_order(vt)
            except Exception as ce:
                out["failed"] += 1
                logger.warning("撤单失败 %s: %s", vt, ce)
    except Exception as e:
        logger.error("撤单流程异常: %s", e)
    return out


# ── 批 171s 件一/件五/件七：同源取价 + 急停审计 + 回执 + 重试编排纯函数 ──
# 旧 XTP_MAX_ORDER_QTY / _latest_close_for（陈 bar）/ 腾讯 _quote_price / 残留
# _close_order_open/_close_order_mark 三个 hunk 全部作废：定价只走同源 hub tick +
# Adapter 接口，拆单上限走 adapter.max_close_order_qty，审计预生成 client_id。

RISK_RESULT_KEY = "risk:cmd:result:{req_id}"
RISK_FINAL_KEY = "risk:cmd:result:{req_id}:final"
RISK_RESULT_TTL_S = 10 * 60
RISK_FINAL_TTL_S = 30 * 60        # 件五：终局键不随 web 5s 回执窗删除


def _quote_price(sym: str, account_id) -> tuple[dict, str]:
    """同源 hub tick 取价封装（件一 get_hub_tick 的调用层，批 171s 新建）。

    返回 (tick, reason)：reason="" 表示 tick 可用（行情 dict 直接喂
    adapter.close_reference_price）；否则 tick={} 且 reason ∈
    valkey_down / bad_json / no_tick（来自 get_hub_tick 的 `_unavailable` 码，可分辨
    系统性 Valkey 全断 vs 单标的暂无 tick）。**不读腾讯 / 成本价 / 陈 bar**——三个出口
    本批全部退役（用户裁定 #1）。never-throw（get_hub_tick account_id=None 硬拒也收口
    成 no_tick + warning 留痕，绝不静默降级）。
    """
    try:
        from src.data_platform.hub_quote import get_hub_tick
        tick = get_hub_tick(account_id, sym)
    except Exception as e:
        logger.warning("全平同源取价失败 %s account=%s: %s", sym, account_id, e)
        return {}, "no_tick"
    if not isinstance(tick, dict):
        return {}, "bad_json"
    reason = str(tick.get("_unavailable") or "")
    return ({} if reason else tick), reason


def _close_client_id(req_id: str, sym: str, seq: int) -> str:
    """预生成拆单 client_id：`emc-{req_id}-{sym}-{seq}`（逐笔递增）。

    全局唯一（req_id 每次急停 uuid12 + sym + 单调 seq）满足 ux_order_log_client_oid；
    INSERT 在 send 之前生成，Order.client_id 与 order_log.client_order_id 用同一 cid。
    """
    return f"emc-{req_id}-{sym}-{seq}"


def _close_order_open(sym: str, volume, price: float, account_id, operator: str,
                      req_id: str, seq: int, action: str,
                      cid: str | None = None) -> tuple[int | None, str]:
    """急停单审计落库（INSERT 在 send 之前；返回 (order_log.id, cid)）。

    写 client_order_id=预生成 cid + status='submitting' + action=派生 action +
    strategy_id='emergency-close'。审计失败不阻断发单（never-raise 留痕）——股灾场景
    「订单先出去」优先，但缺行必须 warning 可见（171 纪律）。operator 保留形参（对齐
    既有调用点），不落列（order_log 无该列）。
    """
    cid = cid or _close_client_id(req_id, sym, seq)
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            oid = conn.execute(
                "INSERT INTO order_log (strategy_id, symbol, action, volume, price, status, "
                "account_id, client_order_id) "
                "VALUES ('emergency-close', %s, %s, %s, %s, 'submitting', %s, %s) RETURNING id",
                (sym, action, volume, price, account_id, cid)).fetchone()[0]
            conn.commit()
        return oid, cid
    except Exception as e:
        logger.warning("全平审计落库失败 %s cid=%s: %s", sym, cid, e)
        return None, cid


def _close_order_mark(order_id, adapter, cid: str, status: str, error: str = "") -> None:
    """终态回填：vt_orderid = adapter.get_vt_orderid(cid)。

    CryptoPerpAdapter / paper（get_vt_orderid 返 None）写 NULL——认领仅靠
    client_order_id 路（write_order_status 的 _vt2cid→client_order_id 反查）。
    绝不把 client_id 冒充 vt_orderid（件七 A2 根治：认领链 + 拒单回执比对）。
    """
    if not order_id:
        return
    try:
        vt = adapter.get_vt_orderid(cid)   # None → 写 NULL
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            # 只进不退（与 write_order_status 同族）：交易所已定局（all_traded/cancelled/
            # rejected）后不得被回写冲掉——成交事件先到再补 mark 时不能把终态改回 submitted。
            # send_failed 不在终态集：首次 mark 从 submitting→send_failed 必须能写进去。
            conn.execute(
                "UPDATE order_log SET status=%s, vt_orderid=%s, error=%s "
                "WHERE id=%s AND status <> ALL(%s)",
                (status, vt, error or None, order_id, list(ORDER_FINAL_STATES)))
            conn.commit()
    except Exception as e:
        logger.warning("全平审计回写失败 %s cid=%s: %s", order_id, cid, e)


def mark_emergency_interrupted(req_id: str) -> None:
    """drain 超时：在飞急停单（submitting）标 interrupted，不留半截平仓无痕（件四）。"""
    if not req_id:
        return
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute(
                "UPDATE order_log SET status='interrupted' "
                "WHERE client_order_id LIKE %s AND status='submitting'",
                (f"emc-{req_id}-%",))
            conn.commit()
    except Exception as e:
        logger.warning("急停单 interrupted 标记失败 req=%s: %s", req_id, e)


def write_cmd_result(req_id: str, result: dict, *, final: bool = False) -> None:
    """急停回执写（never-raise 留痕）：主回执=第一轮快照（web 5s 窗），终局=:final（TTL 30min）。

    Key 格式单点（hub_worker 的 RISK_CMD_RESULT_KEY 只是本常量的别名）。
    """
    if not req_id:
        return
    key = RISK_FINAL_KEY if final else RISK_RESULT_KEY
    ttl = RISK_FINAL_TTL_S if final else RISK_RESULT_TTL_S
    try:
        from src.quant_common.redis_client import business_redis
        r = business_redis(decode_responses=True, socket_timeout=2)
        r.set(key.format(req_id=req_id), json.dumps(result, ensure_ascii=False), ex=ttl)
    except Exception as e:
        logger.warning("急停回执写失败 key=%s req=%s: %s", key, req_id, e)


# ——— 重试编排纯函数（件五：假时钟可测、判定单源、禁两处分叉）———

def next_action(entry: dict, now: float, retry_max: int,
                deadline_ts: float, empty_max: int) -> str:
    """**单一判定源**：返回 'retry' 或 'final:<reason>'。

    三终止条件取先到者：tries>=retry_max（final:retries）/ now>=绝对 deadline
    （final:deadline）/ 连续 no_quote 空轮>=empty_max（final:no_quote）；另
    systemic_down（valkey_down 全断）直接 final:systemic 不空转、pending 清空即
    final:done。调用方按本函数结果决定是否 sleep 再来一轮，不在别处重算终止条件。
    """
    if entry.get("systemic_down"):
        return "final:systemic"
    if not entry.get("pending") and int(entry.get("positions_remain", 0) or 0) <= 0:
        return "final:done"
    if int(entry.get("tries", 0)) >= retry_max:
        return "final:retries"
    if deadline_ts and now >= deadline_ts:
        return "final:deadline"
    if int(entry.get("empty_streak", 0)) >= empty_max:
        return "final:no_quote"
    return "retry"


def retry_due(entry: dict, now: float, interval: float, retry_max: int,
              deadline_ts: float, empty_max: int) -> bool:
    """纯函数：next_action 的布尔视图（判定单源，禁分叉）。

    interval 保留在签名供调用方对齐 sleep 节奏（每轮 sleep(max(1, interval)) 在
    **专用线程内**阻塞推进，故判定本身不依赖 interval）。
    """
    return next_action(entry, now, retry_max, deadline_ts, empty_max) == "retry"


def bump(entry: dict, *, no_quote: bool, systemic_down: bool,
         pending: list, positions_remain: int) -> dict:
    """纯函数：一轮结束后推进重试状态（返回新 dict，不改入参）。

    tries 每轮 +1；no_quote 连续空轮累加、有价轮清零；systemic_down 置终局标志。
    """
    out = dict(entry)
    out["tries"] = int(entry.get("tries", 0)) + 1
    out["pending"] = list(pending)
    out["positions_remain"] = int(positions_remain)
    out["systemic_down"] = bool(systemic_down)
    if systemic_down:
        out["empty_streak"] = 0
    elif no_quote:
        out["empty_streak"] = int(entry.get("empty_streak", 0)) + 1
    else:
        out["empty_streak"] = 0
    return out


def _close_all_positions(adapter, account_id=None, operator: str = "manual",
                         req_id: str = "", deadline_ts: float = 0.0,
                         td_api=None) -> dict:
    """批 171s：一键全平（股灾急停）整轮编排——一轮全平 + 重取价循环重试 + 绝对上限。

    只用**同源 hub tick**（_quote_price → hub_quote.get_hub_tick）与 **Adapter 接口**
    定价/方向/拆单/取整，主流程零 `if 品种/板块` 硬编码分支：
    - action = adapter.close_action(pos) 只算一次，同一值喂 close_reference_price /
      Order / order_log.action / check_order（order 带 reason="emergency_close_all"，
      供 is_flat_or_reduce 放行派生出的 BUY 平空）；
    - 每笔拆单预生成 cid（`emc-{req_id}-{sym}-{seq}`）→ 先 INSERT order_log 再 send_order，
      _close_order_mark 回填 adapter.get_vt_orderid(cid)（None→NULL 靠 client_order_id 认领）；
    - 拒单回执按 **vt_orderid** 比对 query_orders() 的 Status.REJECTED（件七 A2 根治）；
    - 每轮撤未成交单 + **重新 get_hub_tick 取新价** + 重新定价 + 重挂，绝不复用旧价；
      终止三条件取先到者（retry_max / 绝对 deadline / 连续 no_quote 空轮），
      valkey_down 直接升级 _alert 不计空轮。
    **所有 sleep（1.5s 拒单回报窗 / max(1, interval) 轮间隔）都在本函数内**，
    而本函数被 EmergencyWorker 专用 daemon 线程调用 ⇒ 主循环拍内零 sleep。
    第一轮结束即写主回执（web 5s 窗）；终局 :final + SSE 由 hub_worker 负责。
    `td_api`（可选）只用于「已清仓」判定的连通性真源：曾见持仓后须连续两次查到空仓
    且 TD 真连接才算平完（防断线空缓存假报 done）。
    """
    import time as _t
    from vnpy.trader.constant import Status
    from src.risk_control.risk import RiskControl
    from src.data_platform.hub_quote import get_retry_config

    cfg = get_retry_config()
    interval = max(1, int(cfg.get("retry_interval_s", 5) or 5))
    retry_max = int(cfg.get("retry_max", 10) or 10)
    empty_max = int(cfg.get("no_quote_empty_max", 6) or 6)
    # 件五配置返回键（worker1 实装，逐字对齐）：deadline_s
    cfg_deadline = cfg.get("deadline_s", 120)
    if not deadline_ts or deadline_ts <= 0:
        deadline_ts = _t.time() + float(cfg_deadline)

    rc = RiskControl.get()
    out = {"closed_requested": 0, "skipped_t1": [], "rejected": [],
           "positions_remain": 0, "pending": [], "no_quote": [], "retrying": False,
           "final_reason": ""}
    entry = {"tries": 0, "empty_streak": 0, "systemic_down": False,
             "pending": [], "positions_remain": -1}
    seq = 0                       # 拆单序号：整轮单调（cid 全局唯一）
    first_round_written = False
    final_reason = "final:done"
    saw_positions = False   # 本次急停曾见持仓——起始即空仓是真清仓，不启用「连续两次空」门
    empty_hits = 0          # 连续 query_position 空轮（防单次空缓存/断线假报 final:done）

    def _rej(item: str) -> None:
        """rejected 跨轮去重（重试轮会把同一标的同一原因重复列出，no_quote 同款去重）。"""
        if item not in out["rejected"]:
            out["rejected"].append(item)

    while True:
        # ——— 每轮：撤未成交单（上一轮重挂的单先撤，再用新价重挂）———
        _cancel_working_orders(adapter)
        round_no_quote: list[str] = []
        round_priced = False
        round_alerted = False          # valkey_down 本轮只告一次（防按标的刷告警）
        systemic_down = False
        sent: list[tuple[str, str]] = []   # (vt_orderid, symbol)
        try:
            positions = adapter.query_position() or []
        except Exception as e:
            logger.error("全平：持仓查询失败: %s", e)
            positions = []
            round_no_quote.append(":position-query-failed")
        for pos in positions:
            vol = int(getattr(pos, "volume", 0) or 0)
            frozen = int(getattr(pos, "frozen", 0) or 0)
            sellable = max(vol - frozen, 0)
            sym = getattr(pos, "symbol", "")
            if vol <= 0:
                continue   # 多空对冲 NET 口径的空腿/零仓跳过
            saw_positions = True
            action = adapter.close_action(pos)   # 派生方向，只算一次（下面全用同一值）
            # T+1 只约束「卖出」（A 股现货可卖=volume-frozen）；BUY 买回平空不受 T+1 限制，
            # 其可平量=持仓量（原有 frozen 亦为在途卖单冻结，不适用买回）。
            if action == "SELL" and sellable <= 0:
                if sym not in out["skipped_t1"]:
                    out["skipped_t1"].append(sym)
                logger.warning("全平 skip %s：T+1 冻结不可平（volume=%d frozen=%d）", sym, vol, frozen)
                continue
            tick, reason = _quote_price(sym, account_id)
            if reason == "valkey_down":
                systemic_down = True
                if not round_alerted:   # 本轮只告一次（每个标的都告=N 倍告警风暴）
                    round_alerted = True
                    _alert(f"急停全平：同源行情 Valkey 全断（{sym}）",
                           f"account={account_id} req={req_id}；停止空转，转人工处置。",
                           code="emergency.valkey_down")
            if reason:
                round_no_quote.append(f"{sym}:{reason}")
                logger.warning("全平 no_quote %s：%s（不静默降级到腾讯/成本价/陈bar）", sym, reason)
                continue
            try:
                px = adapter.close_reference_price(tick, pos, action)
            except Exception as e:
                logger.warning("全平定价失败 %s: %s", sym, e)
                px = 0.0
            if not px or px <= 0:
                round_no_quote.append(f"{sym}:no-price")
                logger.warning("全平无可用参考价 %s（no_quote 留痕，不静默降级）", sym)
                continue
            try:
                rpx = adapter.round_close_price(px, tick)
            except Exception as e:
                logger.warning("全平取整失败 %s: %s", sym, e)
                rpx = px
            if not rpx or rpx <= 0:
                rpx = px
            round_priced = True
            remaining = sellable if action == "SELL" else vol   # BUY 平空按持仓量（T+1 不适用）
            while remaining > 0:
                try:
                    qty = min(remaining, int(adapter.max_close_order_qty(sym)))
                except Exception as e:
                    logger.warning("全平拆单上限读取失败 %s（回落 100000）: %s", sym, e)
                    qty = min(remaining, 100000)
                if qty <= 0:
                    break
                order = {
                    "symbol": sym, "action": action, "volume": qty, "price": rpx,
                    "reason": "emergency_close_all", "operator": operator,
                    "account_id": account_id,
                    # 有意不带 strategy_id：max_trades_per_day 计数豁免（急停非策略行为）
                }
                d = rc.check_order(order, account_id=account_id)
                if not d.approved:
                    _rej(f"{sym}:{d.reason[:60]}")
                    logger.warning("全平被风控拒 %s (%s): %s", sym, action, d.reason)
                    break
                seq += 1
                cid = _close_client_id(req_id, sym, seq)
                order_id, cid = _close_order_open(sym, qty, rpx, account_id, operator,
                                                  req_id, seq, action, cid)
                try:
                    from src.strategy_framework.adapters import Order
                    ret = adapter.send_order(Order(symbol=sym, action=action, volume=qty,
                                                   price=rpx, order_type="limit", client_id=cid))
                    if not ret:
                        # XTP/EMT 网关拒发/断线时 send_order 返 None（不抛）——必须按失败处理，
                        # 否则虚报「已请求平仓」且 order_log 卡 submitting（P0 虚报成功）。
                        _close_order_mark(order_id, adapter, cid, "send_failed",
                                          error="gateway_rejected")
                        _rej(f"{sym}:not-sent")
                        logger.warning("全平发单未发出（网关拒/断线）%s %s cid=%s", sym, action, cid)
                        break
                    _close_order_mark(order_id, adapter, cid, "submitted")
                    vt = adapter.get_vt_orderid(cid)
                    if vt:
                        sent.append((vt, sym))
                    out["closed_requested"] += 1
                    logger.warning("全平发单 %s %s x%s @%.6f → cid=%s", sym, action, qty, rpx, cid)
                except Exception as se:
                    _close_order_mark(order_id, adapter, cid, "send_failed", error=str(se)[:200])
                    _rej(f"{sym}:{str(se)[:60]}")
                    logger.warning("全平发单失败 %s: %s", sym, se)
                    break
                remaining -= qty
        # ——— 拒单回报窗（专用线程内；每轮重挂后重做）———
        if sent:
            _t.sleep(1.5)
            ids = {vt for vt, _ in sent}
            try:
                for od in (adapter.query_orders() or []):
                    if (getattr(od, "vt_orderid", None) in ids
                            and getattr(od, "status", None) == Status.REJECTED):
                        _rej(f"{getattr(od, 'symbol', '?')}:exchange-rejected")
            except Exception as e:
                logger.warning("全平回执汇总失败: %s", e)
        # ——— 复查剩余仓（pending 真相源）———
        round_pending: list[str] = []
        # 连通性真源（判「已清仓」的前置）：td_api 优先，缺则 adapter，均无按已连接（paper/mock）
        td_ok = bool(getattr(td_api, "connect_status",
                             getattr(adapter, "connect_status", True)))
        try:
            remain_positions = adapter.query_position() or []
            remain = sum(int(getattr(p, "volume", 0) or 0) for p in remain_positions)
            out["positions_remain"] = remain   # 回执语义不变：如实报告剩余量（0/正数/-1）
            round_pending = [f"{getattr(p, 'symbol', '?')}:{int(getattr(p, 'volume', 0) or 0)}"
                             for p in remain_positions if int(getattr(p, "volume", 0) or 0) > 0]
            if remain <= 0:
                empty_hits += 1
                # 本次曾见持仓却读到空：必须连续两次空且 TD 真连接才认「已清仓」——
                # TD 断线/查询零回报时 query_position 清缓存返 []，一次空就 done=假报
                # 「全部平仓完成」（P1-2）。起始即空仓（saw_positions=False）不受此门约束。
                if saw_positions and (empty_hits < 2 or not td_ok):
                    round_pending = [":position-query-unstable"]
                    logger.warning("全平剩余仓判空但未确认（empty_hits=%d td_connected=%s）"
                                   "——按未清仓继续重试", empty_hits, td_ok)
            else:
                empty_hits = 0
        except Exception as e:
            logger.warning("全平剩余仓复查失败: %s", e)
            out["positions_remain"] = -1
            round_pending = [":position-query-failed"]
            empty_hits = 0
        for item in round_no_quote:
            if item not in out["no_quote"]:
                out["no_quote"].append(item)
        out["pending"] = list(round_pending)
        no_quote_round = bool(round_no_quote) and not round_priced
        entry = bump(entry, no_quote=no_quote_round, systemic_down=systemic_down,
                     pending=round_pending, positions_remain=out["positions_remain"])
        now = _t.time()
        due = retry_due(entry, now, interval, retry_max, deadline_ts, empty_max)
        # ——— 第一轮完成即写主回执（web 5s 窗内可见 retrying/pending/no_quote）———
        if not first_round_written:
            first_round_written = True
            snap = dict(out)
            snap.update({"req_id": req_id, "verb": "close-all", "phase": "first",
                         "retrying": bool(due), "interrupted": False})
            write_cmd_result(req_id, snap)
        if not due:
            final_reason = next_action(entry, now, retry_max, deadline_ts, empty_max)
            break
        out["retrying"] = True
        wait = min(interval, max(0.0, deadline_ts - _t.time()))
        if wait > 0:
            _t.sleep(wait)
    # ——— 终局：到上限仍未平则告警（如实列未平清单，不假装成功）———
    out["final_reason"] = final_reason
    if final_reason != "final:done":
        _alert(f"急停全平未竟（{final_reason}）req={req_id}",
               f"account={account_id} pending={out['pending']} no_quote={out['no_quote']} "
               f"positions_remain={out['positions_remain']}",
               code="emergency.incomplete")
    return out


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
            except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
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
    启动与每次 TD 重连沿均变化，知情接受；P1b 改读 domain）。

    v1 策略：发现残留委托只告警不自动撤（防误杀人工单）；成交补录靠 trade_ref 幂等。
    在场判定只读 status（缺状态跳过并留痕）；告警方向用原始对象 .value（locale 无关）。
    """
    try:
        working = []
        for o in (adapter.query_orders() or []):
            try:
                st = status_of(o)
            except Exception as e:
                logger.warning("对账委托状态翻译失败（跳过）: %s", e)
                continue
            if st in (OrderStatus.SUBMITTED, OrderStatus.NOT_TRADED, OrderStatus.PARTIAL):
                working.append(o)
        if working:
            desc = "; ".join(
                f"{getattr(o, 'symbol', '?')} {getattr(getattr(o, 'direction', None), 'value', '?')} "
                f"{getattr(o, 'volume', 0)}@{getattr(o, 'price', 0)}"
                for o in working[:10])
            logger.warning("启动对账：%d 笔在场委托: %s", len(working), desc)
            _alert(f"启动对账发现 {len(working)} 笔在场委托（任务 {sid}）",
                   desc + " —— 疑似上次会话残留，请确认并决定是否人工撤销。", code="reconcile.open-orders")
        trades = adapter.query_trades() or []
        n_new = 0
        for t in trades:
            try:
                dt = to_domain_trade(t, _client_id_of(t, adapter, getattr(t, "vt_orderid", "") or ""))
            except Exception as e:
                logger.warning("对账成交翻译失败（跳过）: %s", e)
                continue
            write_trade_log(dt, adapter, sid, symbol)
            n_new += 1
        if trades:
            logger.info("启动对账：补录当日成交 %d 笔（trade_ref 幂等去重）", n_new)
        # 提交中残留（WAL 崩溃窗口证据）：上一会话 submitting 但无对应成交/委托 → 标记
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT id, symbol, action, volume FROM order_log WHERE strategy_id=%s "
                    "AND status='submitting' AND (ts AT TIME ZONE 'Asia/Shanghai')::date = (now() AT TIME ZONE 'Asia/Shanghai')::date", (sid,))
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
