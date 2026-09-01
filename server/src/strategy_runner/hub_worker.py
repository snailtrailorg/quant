"""hub 模式 worker（ST7，设计 14 v2 §3）。批 4b（2026-08-27）迁 runtime 骨架。

由 strategy_runner.main 在 md_mode=hub 时调用 run()：TD-only 接入 + Valkey Streams 消费
+ SA/SB/SC 机制全复用。交易四件套与 frozen/buy_ok 自 4a 单源化于 strategy_runner.trading；
批 4b：5s 定时段逐项退化为 EngineLoop.every() 钩子（XReadSleeper 双节奏注入）；停止路径=
_stop_hook 清理+os._exit(0)（不用 failure=exit）；心跳只写自有 7 字段+ts（D3）；三件套收编
runtime.alerts。钩子全清单 11 项与知情差异见 docs/任务/批4-worker迁移与trading解耦.md。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

from src.strategy_framework.runtime.alerts import make_alert, make_guard, make_valkey
from src.strategy_framework.runtime.loop import EngineLoop
from src.strategy_framework.runtime.pulse import HeartbeatWriter
from src.strategy_framework.runtime.xsleeper import XReadSleeper
from src.strategy_runner import trading

logger = logging.getLogger("hub_worker")

BAR_STREAM_PREFIX = "hub:bars:"
HB_KEY = "quant:hb:md-hub"
STALE_PUB_S = 60             # pub_ts 超龄丢弃（R-DL3）
# 批 2 骨架三件套（与 hub 同款；_valkey 保留别名供测试/冒烟注入 fake）
_alert = make_alert()
_valkey = make_valkey

def _norm_ts(v) -> str:   # ts 归一化（评审 S4：PG str() 与流 isoformat 断裂）
    try:
        return datetime.fromisoformat(str(v)).isoformat()
    except Exception: return str(v)

class BarMsgState:   # worker 侧消息序号/去重状态（gen 分区内 seq 连续，R-BR6/R-DL2）
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

def _hub_alive(r) -> bool:   # hub 心跳存在（TTL 内）；存储不可查返回 True（断流自然使 bar 过期）
    try:
        return r.exists(HB_KEY) == 1
    except Exception: return True

def _td_connect_due(now: float, win, last_conn_ts: float, dt_now) -> bool:
    """TD 窗开沿建连判定（P2 批 08-28，代码盲审 B-P1）：交易日门（消周末窗读"开"的
    churn 触发）+ 60s 节流（防每步 5s createTraderApi 循环——失败不 release，08-25
    MD 槽回收同型）。纯函数供单测。"""
    if not win or now - last_conn_ts < 60.0:
        return False
    from src.strategy_framework.md_session import is_trading_day, xtp_session_window_open
    return xtp_session_window_open(dt_now, win[0], win[1], trading_day=is_trading_day())


def run(ctx: dict) -> None:
    """ctx: {tid, sid, symbol, strategy, adapter, event_engine, td_api, history, frozen, warmup_pg, stop_check, reconcile}"""
    # 2026-08-19 归位：直连 quant_common（原经 main 互指且连带加载入口模块级 vnpy import）
    from src.quant_common.session import in_astock_session as _in_astock_session, session_edge
    from src.quant_common.guard import sd_notify as _sd_notify

    r = _valkey()
    tid, sid, symbol = ctx["tid"], ctx["sid"], ctx["symbol"]
    strategy, adapter = ctx["strategy"], ctx["adapter"]
    ee = ctx["event_engine"]          # 评审 C1：键名统一
    td_api = ctx.get("td_api")
    history = ctx["history"]
    frozen = ctx["frozen"]            # 与 _run_hub_mode 的 send_order 网关共享同一 dict（评审 C2）
    stream = BAR_STREAM_PREFIX + symbol
    gname = f"task-{tid}"
    cname = f"w-{os.getpid()}"
    state = BarMsgState()
    # last_bar_wall=进程累计；sess_bar_wall=时段内基线（S6 修订：沿上清零，昨夜回放 bar 不污染今晨判定）
    stats = {"last_bar_wall": 0.0, "bars": 0, "dropped_stale": 0, "dropped_dup": 0,
             "sess_bar_wall": 0.0}
    hb_task_key = f"quant:hb:task:{tid}"

    # ——— EVENT_TRADE → trade_log（评审 S1；4a 单源化 trading）———
    @make_guard("hub.on_trade", _alert)
    def on_trade(event):
        trading.write_trade_log(event.data, adapter, sid, symbol)

    ee.register(__import__("vnpy.trader.event", fromlist=["EVENT_TRADE"]).EVENT_TRADE, on_trade)
    # ——— 消费组（先建组后回放，评审陷阱 6）———
    # P0-3 修复（2026-08-20 双盲审计 A1）：重启即销毁旧组重建（新组从 $ 起）——原组残留时 ">" 从旧水位
    # 续消费，重启窗内 bar 重驱动 on_bar=重复下单；丢的 bar 由暖机/gap 从 DB 补，双保险=下方 max_ts 过滤。
    try:
        r.xgroup_destroy(stream, gname)
    except Exception: pass   # 组不存在（首启）属正常
    try:
        r.xgroup_create(stream, gname, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            logger.warning("XGROUP CREATE 失败: %s", e)

    # P0-3 配套：max_ts 持久化恢复（R-DL1 跨重启去重——曾进程内存失忆）
    _mts_key = f"hub:worker:max_ts:{symbol}"
    try:
        _saved = r.get(_mts_key)
        if _saved:
            state.max_ts = _saved
            logger.info("跨重启水位恢复 max_ts=%s", _saved)
    except Exception as e:
        logger.warning("水位恢复读取失败（从空水位起）: %s", e)
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
                                 "low": float(f["low"]), "close": float(f["close"]), "volume": float(f["volume"] or 0)})
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
    # ——— send_order 时刻事实检查（S6）：交易时段+bar 新鲜+hub 心跳；纯逻辑在 trading.buy_ok_check，检查器由 ctx 注入 C2 网关 ———
    ctx["buy_ok"] = lambda: trading.buy_ok_check(frozen, stats, _hub_alive(r), time.time(),
                                                in_session=_in_astock_session())
    # ——— 消息处理（guard 保护，R-BR12；告警走 _alert——notify 自带 1min 同标题去重）———
    @make_guard("hub.on_msg", _alert)
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
            # 数据恢复：重暖机补缺口（从 bar 流触发是正确的——只有消费侧知道缺口多大）
            # 告警：hub 重启是基础设施生命周期事件，由 hub 自身(monitor 断流检测+启动告警)报告，
            #   不从消费侧推断（13号审查设计纠偏+用户架构直觉确认）——worker 重启后首根 gen
            #   从 0 跳到当前值是必然，从这发告警=误报（盲审 A-P2① 的根修）
            logger.info("hub 代次切换 -> gen=%s，重暖机补缺口", state.gen)
            _rewarm(upto_ts=ts_n)
        elif kind == "dup_or_reorder":
            stats["dropped_dup"] += 1
            return
        elif kind == "gap":
            logger.warning("seq 跳变（gap），重暖机并冻结直至人工确认")
            frozen["sticky"] = True   # gap 冻结 sticky（评审 C2：只能重启解）
            _rewarm(upto_ts=ts_n)
            _alert(f"流序号跳变，任务 {tid} 冻结（需重启解冻）", "bar 明细见 bar_hub 表。", code="frozen.stream")
        # pub_ts 超龄丢弃（R-DL3）
        pub_ts = float(fields.get("pub_ts", 0) or 0)
        if pub_ts and _in_astock_session() and (time.time() - pub_ts) > STALE_PUB_S:
            stats["dropped_stale"] += 1
            return
        if str(fields.get("untrusted", "0")).lower() in ("1", "true"):
            frozen["sticky"] = True
            logger.error("untrusted bar（断线失真），冻结: %s", fields.get("ts"))
            _alert(f"不可信 bar，冻结任务 {tid}（{symbol}）", "断线跨分钟失真，重启任务解冻。", code="frozen.stream")
            return
        bar = {"ts": ts_n, "open": float(fields["open"]), "high": float(fields["high"]),
               "low": float(fields["low"]), "close": float(fields["close"]),
               "volume": float(fields["volume"] or 0)}
        # bar 已接受：先落锚+去重水位再驱动策略（盲审 C-F1——锚后更新则首根 bar 的 BUY 读到昨日旧锚被确定性误拒）
        stats["last_bar_wall"] = time.time()
        if _in_astock_session():
            stats["sess_bar_wall"] = stats["last_bar_wall"]
        state.max_ts = ts_n
        try:
            r.set(_mts_key, ts_n)   # P0-3：水位持久化（重启恢复，防 SELL 重放；失败不阻断）
        except Exception: pass
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

    # ——— 批 4b：EngineLoop 编排（旧 5s 定时段逐项退化为钩子；11 项清单/period 见设计）———
    sess_was = _in_astock_session()   # 时段沿检测基态
    td_status_was = True
    _td_conn_ts = [0.0]   # TD 窗开建连 60s 节流锚（P2 批 08-28，B-P1；容器型便 nonlocal 闭包）
    halt_state = {"was": False}
    _baseline_cache = {"baseline": None}   # 账户基线缓存（4a 起调用方持有）
    hb = HeartbeatWriter(r, hb_task_key, ttl=90)

    def _stop_hook():
        """停止路径（设计裁定不用 failure=exit——Restart=on-failure 拉起=F-36 churn 倒退）：
        清理=旧 finally 语义（xgroup_del）+ os._exit(0) 正常停止码（SA4 分类，不触发重启）。"""
        if not ctx["stop_check"]():
            return
        logger.info("任务 %s 收到停止，退出", tid)
        try:
            r.xgroup_del(stream, gname)
        except Exception: pass
        os._exit(0)

    def _sess_edge():
        nonlocal sess_was
        sess_now = _in_astock_session()
        if session_edge(sess_now, sess_was):
            stats["sess_bar_wall"] = 0.0   # S6 修订：沿上清基线
        sess_was = sess_now

    def _blind_watch():
        """盲视观测（S6）：frozen["now"] 只喂心跳/告警，下单判定由 send_order 时刻的 buy_ok 做。"""
        sess_now = _in_astock_session()
        hub_alive = _hub_alive(r)
        bar_stale = (sess_now and stats["sess_bar_wall"]
                     and time.time() - stats["sess_bar_wall"] > trading.FROZEN_STALE_BAR_S)
        new_dyn = (not hub_alive) or bool(bar_stale)
        if new_dyn and not frozen.get("now"):
            logger.error("盲视状态（hub_alive=%s bar_stale=%s）——BUY 将在下单时刻被拒",
                         hub_alive, bool(bar_stale))
            _alert(f"任务 {tid} 盲视（hub{'心跳丢失' if not hub_alive else ' bar 停更'}）",
                   "BUY 在下单时刻被拒/SELL 放行；数据恢复自动解除。", code="buy.blind")
        frozen["now"] = new_dyn or bool(frozen.get("sticky"))

    def _heartbeat():
        """心跳（D3 定案）：只写 worker 自有 7 字段+ts（md 字段区分模式；无 tick 源不冒充）。"""
        hb.beat(pid=os.getpid(), md="hub", gen=state.gen,
                last_bar_ts=stats["last_bar_wall"] or 0,
                lag=(time.time() - stats["last_bar_wall"]) if stats["last_bar_wall"] else -1,
                bars=stats["bars"], frozen=int(frozen.get("now", False)))

    def _td_reconnect():
        """TD 重连沿 → 重跑对账（R-BR11；ctx["reconcile"]=runner 超集含成交补录，4b 收敛冗余循环）。
        窗开沿建连（P2 批 08-28，A-P1-1）：窗开且未连 → ctx["td_connect"]()——盘外窗关
        启动的进程由此腿补首连，连上后下一沿触发对账（B-P1-1 启动对账条件化的配对）。"""
        nonlocal td_status_was
        td_status = bool(getattr(td_api, "connect_status", True))
        if td_status and not td_status_was:
            logger.info("TD 重连沿，重跑对账（含成交补录）")
            try:
                ctx["reconcile"]()
            except Exception as e:
                logger.warning("重连对账失败: %s", e)
        if not td_status:
            from datetime import datetime as _dt
            if _td_connect_due(time.time(), ctx.get("td_window"), _td_conn_ts[0], _dt.now()):
                _td_conn_ts[0] = time.time()
                logger.info("TD 连接窗开沿，建连（60s 节流+交易日门）")
                try:
                    ctx["td_connect"]()
                except Exception as e:
                    logger.warning("TD 窗开建连失败（60s 后重试）: %s", e)
        td_status_was = td_status

    def _zombie_claim():
        """僵尸 pending 认领并处理（评审 S3：认领即消费，幂等靠 ts 去重）。"""
        try:
            _next, claims = r.xautoclaim(stream, gname, cname, min_idle_time=60000, count=20)
            if claims:
                process_batch([(stream, claims)])
        except Exception: pass

    loop = EngineLoop(
        name=f"live-task-{tid}", step=5.0,
        sleeper=XReadSleeper(r, stream, gname, cname, process_batch),  # 流消费=sleeper 注入
        watchdog=lambda: _sd_notify("WATCHDOG=1"), event_engines=(ee,),  # 喂狗+事件线程存活（R-BR12）
        on_fatal=lambda reason: _alert(f"任务 {tid} {reason}，自动重启",
                                       "worker 退出由 systemd 接管；请查 journalctl 定位首个异常。",
                                       code="runtime.fatal"))
    loop.every("stop-check", 5.0, _stop_hook)        # 停止（P4-3；清理+exit 0 在钩子内）
    loop.every("sess-edge", 0.0, _sess_edge)         # 时段沿清 sess_bar_wall 基线：每步
    loop.every("blind-watch", 0.0, _blind_watch)     # 盲视判定+告警（喂 frozen 字段）：每步
    loop.every("heartbeat", 5.0, _heartbeat)         # 心跳（D3 七字段+ts）
    loop.every("snapshot", 60.0, lambda: trading.snapshot_cycle(  # 快照+持仓批（旧 12 拍=60s）
        adapter, ctx.get("account_id"), tid, _baseline_cache))
    loop.every("halt-edge", 0.0, lambda: trading.halt_edge_cancel(adapter, halt_state, sid))  # 熔断沿撤在场单
    loop.every("factor-recalc", 5.0, lambda: trading.recalc_hook(r, _rewarm, history))  # 因子重算+热重载
    loop.every("td-reconnect", 0.0, _td_reconnect)   # TD 重连沿对账：每步
    loop.every("zombie-claim", 5.0, _zombie_claim)   # xautoclaim 僵尸认领（评审 S3）
    try:
        loop.run()   # 永续（XReadSleeper never-raise 保证 sleep 位不抛；停止/NOGROUP 带码直达）
    except KeyboardInterrupt:
        pass
    finally:   # 原生库拆除规避（同 hub/direct）；正常停止/NOGROUP 已在钩子/sleeper 内带码直达
        try:
            r.xgroup_del(stream, gname)
        except Exception:
            pass
        os._exit(0)
