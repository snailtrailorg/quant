"""hub 模式 worker（ST7，设计 14 v2 §3）。评审③修订版。

由 strategy_runner.main 在 md_mode=hub 时调用 run()：TD-only 接入 + Valkey Streams 消费
+ SA/SB/SC 机制全复用（含 direct 主循环四件套：trade_log/快照/熔断沿/recalc，评审 S1）。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

logger = logging.getLogger("hub_worker")

BAR_STREAM_PREFIX = "hub:bars:"
HB_KEY = "quant:hb:md-hub"
FROZEN_STALE_BAR_S = 300     # 交易时段无新 bar 冻结（评审 S6 worker 侧防线）
STALE_PUB_S = 60             # pub_ts 超龄丢弃（R-DL3）


def _valkey():
    import redis
    return redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
                                decode_responses=True, socket_timeout=3)


def _norm_ts(v) -> str:
    """ts 归一化（评审 S4：PG str() 与流 isoformat 格式断裂——统一 fromisoformat→isoformat）。"""
    try:
        return datetime.fromisoformat(str(v)).isoformat()
    except Exception:
        return str(v)


class BarMsgState:
    """worker 侧消息序号/去重状态（gen 分区内 seq 连续，R-BR6/R-DL2）。"""

    def __init__(self):
        self.gen = 0
        self.seq = 0
        self.max_ts: str = ""       # 本标的已处理最大 ts（R-DL1 持久去重，评审 S7 真正使用）

    def classify(self, m: dict) -> str:
        gen, seq = int(m["gen"]), int(m["seq"])
        if gen < self.gen:
            return "stale_gen"
        if gen > self.gen:
            self.gen, self.seq = gen, seq
            return "gen_jump"
        if seq <= self.seq:
            return "dup_or_reorder"
        if seq != self.seq + 1:
            self.seq = seq   # 接受跳变（gap 由调用方回放补齐）
            return "gap"
        self.seq = seq
        return "ok"


def _hub_alive(r) -> bool:
    """hub 心跳存在（TTL 内）。存储不可查时返回 True——断流会自然使 bar 过期，不在此路径阻断。"""
    try:
        return r.exists(HB_KEY) == 1
    except Exception:
        return True


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


def _write_trade_log(d, adapter, sid: str, symbol: str) -> None:
    """TradeData → trade_log（SC1 同款，供 EVENT_TRADE 与启动/重连对账共用）。幂等 trade_ref。"""
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
            conn.execute(
                "INSERT INTO trade_log (ts, strategy_id, order_id, symbol, action, volume, price, trade_ref) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (trade_ref) DO NOTHING",
                (getattr(d, "datetime", None), strategy_of, order_db_id, getattr(d, "symbol", symbol),
                 action, float(getattr(d, "volume", 0) or 0), float(getattr(d, "price", 0) or 0),
                 getattr(d, "vt_tradeid", None) or None))
            conn.commit()
    except Exception as e:
        logger.warning("trade_log 写入失败: %s", e)


def run(ctx: dict) -> None:
    """ctx: {tid, sid, symbol, strategy, adapter, event_engine, td_api, history, frozen,
             warmup_pg, stop_check, reconcile, initial_capital, logger}"""
    from src.strategy_runner.main import _guard, _sd_notify, _alert, _in_astock_session, session_edge

    r = _valkey()
    tid = ctx["tid"]
    sid = ctx["sid"]
    symbol = ctx["symbol"]
    strategy = ctx["strategy"]
    adapter = ctx["adapter"]
    ee = ctx["event_engine"]          # 评审 C1：键名统一
    td_api = ctx.get("td_api")
    history = ctx["history"]
    frozen = ctx["frozen"]            # 与 _run_hub_mode 的 send_order 网关共享同一 dict（评审 C2）
    initial_capital = ctx.get("initial_capital") or 1000000
    stream = BAR_STREAM_PREFIX + symbol
    gname = f"task-{tid}"
    cname = f"w-{os.getpid()}"
    state = BarMsgState()
    # last_bar_wall=进程累计；sess_bar_wall=时段内基线（S6 修订：沿上清零，昨夜回放 bar 不污染今晨判定）
    stats = {"last_bar_wall": 0.0, "bars": 0, "dropped_stale": 0, "dropped_dup": 0,
             "sess_bar_wall": 0.0}
    hb_task_key = f"quant:hb:task:{tid}"

    # ——— EVENT_TRADE → trade_log（评审 S1 四件套之一）———
    @_guard("hub.on_trade")
    def on_trade(event):
        _write_trade_log(event.data, adapter, sid, symbol)

    ee.register(__import__("vnpy.trader.event", fromlist=["EVENT_TRADE"]).EVENT_TRADE, on_trade)

    # ——— 消费组（先建组后回放，评审陷阱 6）———
    try:
        r.xgroup_create(stream, gname, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            logger.warning("XGROUP CREATE 失败: %s", e)

    # ——— 暖机：只填 history 绝不调 on_bar（评审 F3）———
    def _warmup_from_stream(hist: list, upto_ts: str | None = None) -> list:
        """流回放填 history。upto_ts 截断（rewarm 时防未来泄漏，评审 S4）。"""
        try:
            entries = r.xrevrange(stream, count=240)
            seen = {_norm_ts(h.get("ts")) for h in hist}
            bars = []
            for _id, f in reversed(entries):
                ts_n = _norm_ts(f.get("ts", ""))
                if upto_ts and ts_n > upto_ts:
                    continue                       # 只灌当前消息之前的（防未来）
                if ts_n and ts_n not in seen:
                    bars.append({"ts": ts_n, "open": float(f["open"]), "high": float(f["high"]),
                                 "low": float(f["low"]), "close": float(f["close"]),
                                 "volume": float(f["volume"] or 0)})
                    seen.add(ts_n)
            hist.extend(bars)
            logger.info("hub 暖机：流回放补 %d 根（history 总 %d）", len(bars), len(hist))
        except Exception as e:
            logger.warning("hub 暖机回放失败: %s", e)
        return hist

    def _rewarm(upto_ts: str | None = None) -> None:
        fresh = ctx["warmup_pg"]()
        fresh = _warmup_from_stream(fresh, upto_ts)
        history[:] = fresh[-100:]

    _rewarm()   # 初始暖机（消费组建在 $，流内现有 bar 全部是"过去"，无未来泄漏）

    # ——— send_order 时刻事实检查（S6 修订）：交易时段 + bar 新鲜（<300s）+ hub 心跳 ———
    # 检查器由 ctx 注入 C2 网关（main._gated_send）；纯逻辑在模块级 buy_ok_check 供测试
    ctx["buy_ok"] = lambda: buy_ok_check(frozen, stats, _hub_alive(r), time.time(),
                                         in_session=_in_astock_session())

    def _alert_throttled(title: str, body: str) -> None:
        _alert(title, body)   # notify 自带 1min 同标题去重

    # ——— 消息处理（guard 保护，R-BR12）———
    @_guard("hub.on_msg")
    def handle_msg(fields: dict) -> None:
        ts_n = _norm_ts(fields.get("ts", ""))
        # R-DL1 持久去重（评审 S7）：ts 回退/重复（含 flush 迟到 tick 重复桶）一律丢弃
        if ts_n and ts_n <= state.max_ts:
            stats["dropped_dup"] += 1
            return
        kind = state.classify(fields)
        if kind == "stale_gen":
            logger.warning("拒绝旧代次消息 gen=%s < %s（fencing）", fields["gen"], state.gen)
            return
        if kind == "gen_jump":
            logger.info("hub 代次切换 -> gen=%s，重暖机补缺口", state.gen)
            _rewarm(upto_ts=ts_n)
            _alert_throttled(f"行情 hub 重启（代次 {state.gen}），任务 {tid} 已补暖机", "")
        elif kind == "dup_or_reorder":
            stats["dropped_dup"] += 1
            return
        elif kind == "gap":
            logger.warning("seq 跳变（gap），重暖机并冻结直至人工确认")
            frozen["sticky"] = True   # gap 冻结 sticky（评审 C2：只能重启解）
            _rewarm(upto_ts=ts_n)
            _alert_throttled(f"流序号跳变，任务 {tid} 冻结（需重启解冻）", "bar 明细见 bar_hub 表。")
        # pub_ts 超龄丢弃（R-DL3）
        pub_ts = float(fields.get("pub_ts", 0) or 0)
        if pub_ts and _in_astock_session() and (time.time() - pub_ts) > STALE_PUB_S:
            stats["dropped_stale"] += 1
            return
        if str(fields.get("untrusted", "0")).lower() in ("1", "true"):
            frozen["sticky"] = True
            logger.error("untrusted bar（断线失真），冻结: %s", fields.get("ts"))
            _alert_throttled(f"不可信 bar，冻结任务 {tid}（{symbol}）", "断线跨分钟失真，重启任务解冻。")
            return
        bar = {"ts": ts_n, "open": float(fields["open"]), "high": float(fields["high"]),
               "low": float(fields["low"]), "close": float(fields["close"]),
               "volume": float(fields["volume"] or 0)}
        # bar 已接受：先落锚+去重水位，再驱动策略（盲审 C-F1 2026-08-18——place_order 在 on_bar
        # 内同步执行，若锚在 on_bar 之后更新，开盘/午后首根 bar 的 BUY 会读到昨日旧锚被确定性误拒）
        stats["last_bar_wall"] = time.time()
        if _in_astock_session():
            stats["sess_bar_wall"] = stats["last_bar_wall"]
        state.max_ts = ts_n
        sig = strategy.on_bar(bar, list(history))
        stats["bars"] += 1
        history.append(bar)
        if len(history) > 100:
            history.pop(0)
        sa = getattr(sig, "action", None)
        logger.info("BAR %s close=%.2f vol=%.0f signal=%s", ts_n[:16], bar["close"], bar["volume"],
                    sa.name if sa else "NONE")

    def process_batch(batch) -> int:
        ids = []
        for _stream, entries in batch:
            for eid, f in entries:
                handle_msg(f)
                ids.append(eid)
        if ids:
            try:
                r.xack(stream, gname, *ids)
            except Exception as e:
                logger.warning("XACK 失败: %s", e)
        return len(ids)

    # ——— 主循环（评审 S7 伪代码；TimeoutError 归 RedisError，评审 B3）———
    last_timer = 0.0
    sess_was = _in_astock_session()   # 时段沿检测（S6 修订：沿上清 sess_bar_wall 基线）
    td_status_was = True
    halt_state = {"was": False}
    snap_counter = 0
    try:
        while True:
            try:
                batch = r.xreadgroup(gname, cname, {stream: ">"}, count=10, block=500)
                if batch:
                    process_batch(batch)
            except Exception as e:
                # 连接异常（含 redis TimeoutError）睡 1s 重试；BLOCK 超时返回空表不走这
                if "Timeout" not in type(e).__name__:
                    logger.warning("XREADGROUP 异常: %s", e)
                    time.sleep(1)
            if time.time() - last_timer < 5:
                continue
            last_timer = time.time()
            snap_counter += 1
            _sd_notify("WATCHDOG=1")
            if ctx["stop_check"]():
                logger.info("任务 %s 收到停止，退出", tid)
                break
            # hub 心跳 + bar 停更 → 盲视观测（S6 修订：frozen["now"] 只喂心跳/告警，
            # 下单判定在 send_order 时刻由 buy_ok 完成；基线=时段内 bar，跨日/假日不误报）
            sess_now = _in_astock_session()
            if session_edge(sess_now, sess_was):
                stats["sess_bar_wall"] = 0.0
            sess_was = sess_now
            hub_alive = _hub_alive(r)
            bar_stale = (sess_now and stats["sess_bar_wall"]
                         and time.time() - stats["sess_bar_wall"] > FROZEN_STALE_BAR_S)
            new_dyn = (not hub_alive) or bool(bar_stale)
            if new_dyn and not frozen.get("now"):
                logger.error("盲视状态（hub_alive=%s bar_stale=%s）——BUY 将在下单时刻被拒",
                             hub_alive, bool(bar_stale))
                _alert_throttled(f"任务 {tid} 盲视（hub{'心跳丢失' if not hub_alive else ' bar 停更'}）",
                                 "BUY 在下单时刻被拒/SELL 放行；数据恢复自动解除。")
            frozen["now"] = new_dyn or bool(frozen.get("sticky"))
            # 心跳（R-OBS2）
            try:
                r.hset(hb_task_key, mapping={
                    "pid": os.getpid(), "md": "hub", "gen": state.gen,
                    "last_bar_ts": stats["last_bar_wall"] or 0,
                    "lag": (time.time() - stats["last_bar_wall"]) if stats["last_bar_wall"] else -1,
                    "bars": stats["bars"], "frozen": int(frozen.get("now", False)),
                })
                r.expire(hb_task_key, 90)
            except Exception as e:
                logger.warning("心跳写失败: %s", e)
            # 快照（每 60s≈12 个 timer，评审 S1；断线不写假值 SB1）
            if snap_counter % 12 == 0:
                try:
                    accounts = adapter.query_account() or []
                    if accounts:
                        from src.data_platform.db import get_conn
                        total = sum(float(getattr(a, "balance", 0)) for a in accounts)
                        today = datetime.now().strftime("%Y-%m-%d")
                        with get_conn() as conn:
                            cur = conn.execute("SELECT total_value FROM account_snapshot WHERE ts::date=%s "
                                               "ORDER BY ts ASC LIMIT 1", (today,))
                            first = cur.fetchone()
                            base = float(first[0]) if first else total
                            conn.execute("INSERT INTO account_snapshot (total_value, daily_pnl, initial_capital) "
                                         "VALUES (%s,%s,%s)",
                                         (total, total - base, initial_capital))
                            conn.commit()
                except Exception as e:
                    logger.warning("快照写失败: %s", e)
            # 熔断沿撤在场单（评审 S1，F-41）
            try:
                from src.risk_control.risk import RiskControl
                halted_now = RiskControl.get().is_halted()
            except Exception:
                halted_now = halt_state["was"]
            if halted_now and not halt_state["was"]:
                logger.critical("检测到熔断，撤销全部在场委托")
                _alert_throttled(f"熔断触发，已撤在场委托: {sid}", "")
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
                    logger.error("熔断撤单异常: %s", e)
            halt_state["was"] = halted_now
            # factor:recalc（评审 S1）
            try:
                if r.get("factor:recalc:triggered"):
                    _rewarm()
                    r.delete("factor:recalc:triggered")
                    logger.info("因子重算触发：重暖机 %d 根", len(history))
            except Exception as e:
                logger.warning("recalc 检查失败: %s", e)
            # TD 重连沿 → 重跑对账（R-BR11）+ 成交补录
            td_status = bool(getattr(td_api, "connect_status", True))
            if td_status and not td_status_was:
                logger.info("TD 重连沿，重跑对账+成交补录")
                try:
                    ctx["reconcile"]()
                    for t in (adapter.query_trades() or []):
                        _write_trade_log(t, adapter, sid, symbol)
                except Exception as e:
                    logger.warning("重连对账失败: %s", e)
            td_status_was = td_status
            # 事件线程存活（R-BR12）
            et = getattr(ee, "_thread", None)
            if et is not None and not et.is_alive():
                logger.critical("worker 事件线程死亡，退出待重启")
                _alert(f"hub worker 事件线程死亡（任务 {tid}），自动重启", "")
                os._exit(1)
            # 僵尸 pending 认领并处理（评审 S3：认领即消费，幂等靠 ts 去重）
            try:
                _next, claims = r.xautoclaim(stream, gname, cname, min_idle_time=60000, count=20)
                if claims:
                    process_batch([(stream, claims)])
            except Exception:
                pass
    finally:
        try:
            r.xgroup_del(stream, gname)
        except Exception:
            pass
        os._exit(0)
