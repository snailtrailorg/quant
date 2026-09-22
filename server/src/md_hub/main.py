"""共享行情 Hub（ST7，设计 docs/architecture/14 v2）。

单进程持 XTP MD 连续 + 全市场合约，tick→MinuteAggregator→Valkey Streams 分发。
纯数据面：无下单/无风控（R-HALT1）；零 TD 会话。
启动: python -m src.md_hub.main；systemd: quant-md-hub@quant（单元在 server/scripts/systemd/）。

关键机制（对齐需求书 R-*）：租约+gen（R-DL4）/分钟末标注（R-BR9）/累计差分（S3）/
双 flush（S2）/untrusted 双门限（R-BR4）/bar 落 bar_hub（R-CAP3/F2）/心跳+看门狗
（R-AV1/S6）——数据面部件在 parts.py。
批 2（2026-08-25）：主循环迁上 runtime 骨架——EngineLoop 到期驱动钩子（废 counter%N
相位耦合），L2 会话自愈五段收编 MdSessionSupervisor；行为值不变（AlertPolicy 默认
=hub 现值，心跳字段超集）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

from src.md_hub.parts import (   # 数据面部件（批 2 原样移驻；import 即重导出保测试路径——ThinGateway 批 63 二收编 md_gateway 后不再经 main 重导出）
    ACTIVE_INSTANCE_KEY,
    INTENT_KEY,
    LATEST_TICK_PREFIX,
    LEASE_KEY,
    MinuteAggregator,
    _LEASE_RENEW_LUA,
    _PGWriter,
    _in_bar_session,
    _lease_acquire_guarded,
    _lease_boot,
    _lease_release,
    _project_symbol,
    _write_latest_tick,
)

logger = logging.getLogger("md_hub")

try:
    from vnpy.event import EventEngine
except ImportError:
    EventEngine = None

# 2026-08-19 模块归位：共享工具直连 quant_common（原寄生 strategy_runner.main——连带 vnpy 链）
from src.quant_common.session import in_astock_session as _in_astock_session
from src.quant_common.guard import sd_notify as _sd_notify
from src.strategy_framework.runtime.alerts import make_alert, make_guard, make_valkey

__all__ = ["LATEST_TICK_PREFIX", "main"]   # LATEST_TICK_PREFIX 仅重导出（test_stock_detail 经 main 取用）

# _alert/_guard/_valkey 三件套批 2 收编 runtime.alerts（hub 原实现逐语句等价，行为不变）
_alert = make_alert()


def _guard(name):
    return make_guard(name, _alert)


BAR_STREAM_PREFIX = "hub:bars:"
HB_KEY = "quant:hb:md-hub"
STREAM_MAXLEN = 5000          # ≈20 交易日分钟 bar（评审：慢消费者 3 周不读才可能被剪）


def _read_intent(r):
    """M5：读 hub:switch:intent，返回 {snapshot, target} dict 或 None（键不存在/坏值/不可达）。"""
    try:
        raw = r.get(INTENT_KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        import json as _json
        data = _json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("target", ""), str) or not isinstance(data.get("snapshot"), (int, float)):
        return None
    return data


def _boot_dispatch(r, instance_name: str) -> tuple[str, int]:
    """M5 boot 单判定 + dispatch 分叉（先仲裁再拿权，插在一切行情初始化之前）。

    intent 存在：target==自己→guarded（切换目标接管）；target≠自己→exit(6)（被切走者拦截）。
    intent 不存在：active_instance≠自己→exit(6)（非现任不抢回）；否则→normal 冷启（SET active_instance）。
    """
    import secrets
    intent = _read_intent(r)
    if intent is not None:
        target = intent.get("target", "")
        if target != instance_name:
            logger.error("切换窗内被切走者（target=%s 本=%s），exit 6", target, instance_name)
            raise SystemExit(6)
        expected_gen = int(intent.get("snapshot", 0)) + 1
        my_uuid = secrets.token_hex(8)   # M5：B 的 uuid 运行时生成（active_instance==target 校验替代 holder==uuid，无需预定）
        for _attempt in range(3):
            ok, uuid_, gen = _lease_acquire_guarded(r, expected_gen, target, my_uuid)
            if ok:
                return uuid_, gen
            if gen == -1:   # gen 污染（复活 A 抢前 INCR）→ 拒接管，exit 1 让 systemd 重拉
                logger.error("guarded 拒接管（gen 污染，期望=%d），exit 1", expected_gen)
                raise SystemExit(1)
            time.sleep(5)   # -2 旧 lease 挡（旧化身 TTL 30s 未过期）→ 重试
        logger.error("guarded 3 次重试耗尽（旧 lease 挡或存储不可达），exit 1")
        raise SystemExit(1)
    # intent 不存在：active_instance 仲裁
    active = None
    try:
        active = r.get(ACTIVE_INSTANCE_KEY)
    except Exception:
        active = None
    if active and active != instance_name:
        logger.error("非现任（active_instance=%s 本=%s），exit 6", active, instance_name)
        raise SystemExit(6)
    return _lease_boot(r, instance_name)


def main() -> None:
    # #48：启动时列级校验（hub 侧同款）
    try:
        from src.data_platform.db import verify_schema
        from src.health_monitor.monitor import report_schema_findings
        report_schema_findings(verify_schema())
    except Exception as e:
        logger.warning("schema 校验异常（不阻断启动）: %s", e)

    # 批25：system_log 落库（md-hub 装配：source=hub；SIGTERM 链式冲刷）
    import signal as _sig
    from src.data_platform.log_sink import install as _log_install, chain_sigterm as _chain
    _log_install("hub")
    _chain(_sig.getsignal(_sig.SIGTERM))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if EventEngine is None:
        logger.error("vnpy 未安装")
        raise SystemExit(1)

    r = make_valkey()

    # ——— M5 boot 单判定 + dispatch（先仲裁再拿权；重试/让位/退出语义在 parts）———
    instance_name = os.environ.get("INSTANCE_NAME", "")
    my_uuid, gen = _boot_dispatch(r, instance_name)
    logger.info("hub 启动：uuid=%s gen=%d 实例=%s", my_uuid, gen, instance_name or "(空)")

    # ——— 行情网关（批 63 二：插件化——XTP 全套收编 md_gateway.XtpMdGateway，hub 只持通用面）———
    from src.strategy_framework.md_session import is_trading_day
    from src.strategy_framework.runtime.loop import EngineLoop
    from src.strategy_framework.runtime.pulse import HeartbeatWriter, SessionCounters
    from src.strategy_framework.runtime.subs import SubscriptionManager

    agg = MinuteAggregator()
    seqs: dict[str, int] = {}
    seqs_lock = threading.Lock()   # 评审 B4：事件线程 on_tick 与主循环 flush 并发 _publish
    _lt_fail_ts: dict[str, float] = {}   # latest_tick 连败退避表（O 审 M6）
    # ticks/bars/last_tick_wall=进程累计（心跳字段，原渠道保留）；
    # 时段内基线（sess_*/enter_ts/沿清零，S6 修订）批 2 起单点化 SessionCounters（事故 1 根治）
    stats = {"ticks": 0, "bars": 0, "last_tick_wall": 0.0}
    counters = SessionCounters()
    pgw = _PGWriter()
    pgw.start()

    @_guard("hub.on_tick")
    def on_tick(tick):
        symbol = _project_symbol(tick)
        stats["ticks"] += 1
        stats["last_tick_wall"] = time.time()
        if _in_astock_session():
            # 只在盘中喂 on_data：旧 sess_last_tick 仅盘中写入——盘外回放不建断流基线，
            # supervisor 的断流症状/告警不会在盘外（夜间回放停止/假日静默）误触（行为值不变铁律）
            counters.on_data(True)
        _write_latest_tick(r, symbol, tick, _lt_fail_ts)
        if not _in_bar_session(tick.datetime):
            # P2 修复批（08-28 双轨四分类②④）：盘前/午休尾/收盘后快照不进聚合器。
            # 位置钉死（盲审 A-P1-2/B-P2-3）：latest_tick/stats/counters 已执行照常，
            # 仅拦 agg 喂入——盘前快照照上详情页，心跳字段语义不变。
            return
        bar = agg.on_tick(symbol, tick)
        if bar:
            _publish(bar)

    def _publish(bar: dict) -> None:
        with seqs_lock:
            try:
                r.xadd(BAR_STREAM_PREFIX + bar["symbol"], msg_of(bar, seqs.get(bar["symbol"], 0) + 1),
                       maxlen=STREAM_MAXLEN, approximate=True)
                seqs[bar["symbol"]] = seqs.get(bar["symbol"], 0) + 1   # 评审 B1：成功后才占号（失败不留洞）
            except Exception as e:
                logger.error("XADD 失败（bar 丢失，告警）: %s", e)
                _alert("hub XADD 失败（bar 丢失）", f"{bar['symbol']} {bar['ts']}", code="hub.xadd-fail")
                return
        stats["bars"] += 1
        pgw.push(bar)

    def msg_of(bar: dict, seq: int) -> dict:
        return {
            "gen": gen, "seq": seq,
            "ts": bar["ts"].isoformat(), "pub_ts": time.time(),
            "untrusted": int(bar.get("untrusted", False)),
            "open": bar["open"], "high": bar["high"], "low": bar["low"], "close": bar["close"],
            "volume": bar["volume"], "amount": bar["amount"], "tick_count": bar["tick_count"],
        }

    # ——— 连接 + 订阅（真相源=DB，15s diff + 60s 幂等重放，R-SUB；批 63 二：接口行 provider 选网关插件）———
    interface_row = os.environ.get("HUB_INTERFACE_ROW", "")
    row_id = int(interface_row) if interface_row else None
    try:
        from src.strategy_framework.broker import get_interface_row
        iface = get_interface_row(row_id)
    except Exception as e:
        logger.error("外部接口行取数失败（HUB_INTERFACE_ROW=%s），exit 78: %s", interface_row, e)
        raise SystemExit(78)
    if row_id is not None and not iface["credentials"]:
        # M5 语义保留：B 实例指定行必须带凭证，禁 .env fallback（防 B 静默跑 A 账号）
        logger.error("接口行 id=%s 无凭证（B 实例 fail-fast），exit 78", row_id)
        raise SystemExit(78)
    from src.strategy_framework.md_gateway import create_md_gateway
    md_gw = create_md_gateway(iface["provider"], counters)
    md_gw.set_on_tick(on_tick)   # tick 喂入（EVENT_TICK 注册收编插件内）
    md_gw.connect(iface["credentials"], iface["params"])

    md_status_was = False   # MD 重连沿基态（SA2 hub 版；connected 由网关插件供）

    def _desired_symbols() -> set[str]:
        """订阅真相源（三源）：running 任务标的 ∪ system_config 白名单 ∪ 临时订阅。

        池源已移除（2026-09-04 分钟数据源重构 21 号 §3.3）：历史分钟数据改腾讯攒
        （bar_1min），hub 不再为攒数据订阅非实盘标的，释放 XTP 订阅额度。
        临时源（2026-08-20 三档详情页"看过即订阅"，用户裁定 XTP 为主路径）：expire_at>now
        的行——过期即不可见=自动退订（30min TTL 由详情页每次打开续期）。
        """
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:   # 影子查询必须在 with 内——曾因缩进在块外用到已还池连接被静默吞（2026-08-17 实测 subs=0）
                cur = conn.execute(
                    "SELECT DISTINCT symbol FROM live_task WHERE status='running' AND symbol IS NOT NULL")
                rows = {x[0] for x in cur.fetchall() if x[0]}
                try:
                    cur = conn.execute("SELECT value FROM system_config WHERE key='hub_shadow_symbols'")
                    row = cur.fetchone()
                    if row and row[0]:
                        rows |= {s.strip() for s in row[0].split(",") if s.strip()}
                except Exception as e:
                    logger.warning("读 hub_shadow_symbols 失败: %s", e)
                # 临时源（详情页看过即订阅，TTL 自动退订；顺带清理过期行防表膨胀）
                try:
                    cur = conn.execute(
                        "SELECT symbol FROM hub_transient_subs WHERE expire_at > now()")
                    trans_rows = {x[0] for x in cur.fetchall() if x[0]}
                    if trans_rows:
                        rows |= trans_rows
                        logger.debug("临时源订阅 +%d 标的", len(trans_rows))  # 降噪同上
                    conn.execute("DELETE FROM hub_transient_subs WHERE expire_at <= now()")
                    conn.commit()
                except Exception as e:
                    logger.warning("读临时订阅源失败: %s", e)
                return rows
        except Exception as e:
            logger.warning("读订阅真相源失败（沿用旧集）: %s", e)
            return sm.current   # 旧集=当前已同步集（sm 定义在下方，运行时已存在）

    def _subscribe(sym: str) -> None:
        try:
            md_gw.subscribe(sym)   # 订阅原语=网关插件（XTP 的交易所映射在 XtpMdGateway）
        except Exception as e:
            logger.warning("订阅失败 %s: %s", sym, e)

    def _unsubscribe(sym: str) -> None:
        """退订（生命周期闭环：出池/临时订阅过期/白名单摘除/live_task 停）。

        先 flush 在桶分钟防丢最后一根（hub 通用面），再网关退订原语（插件）——XTP 的
        EXCHANGE 映射/LOGGED_IN 态闸/跳过后重连全量重放兜底语义全在 XtpMdGateway.unsubscribe。
        """
        try:
            bar = agg.flush_symbol(sym)
            if bar:
                _publish(bar)
            md_gw.unsubscribe(sym)
        except Exception as e:
            logger.warning("退订失败 %s: %s", sym, e)

    # 订阅管理收编骨架（批 2）：旧 _sync_subscriptions 的 diff/全量重放/重连沿/退订 flush
    # 语义原样（SubscriptionManager），节奏由下方钩子注册——15s diff / 60s 全量重放
    # （替换 %60<10 窗口法：同效果，无相位耦合）
    sm = SubscriptionManager(desired=_desired_symbols, subscribe=_subscribe, unsubscribe=_unsubscribe)
    md_gw.set_context(lambda: f"订阅 {len(sm.current)} 个标的。")   # 告警上下文（监督器文案；监督器批 63 二收编网关内）
    if md_gw.start_ready():
        sm.replay()   # 启动全量订阅（旧 _sync_subscriptions(force=True)）
        logger.info("hub 就绪，初始订阅 %d", len(sm.current))
    else:
        # 窗关启动（P2 批 08-28）：订阅不预放（guard 非 LOGGED_IN 态 no-op，账实会错）——
        # 窗开沿 relogin → connected True → _md_edge 上升沿强制全量重放，账实自然对齐
        logger.info("hub 窗关启动（provider=%s，defer_login），订阅待窗开沿重放", iface["provider"])

    # ——— 主循环（批 2：EngineLoop 到期驱动；喂狗/事件线程存活检查内建骨架）———
    def _md_edge() -> None:
        """MD 重连沿：connected 上升沿 → 强制全量重放（XTP 重连不恢复订阅；connected 由网关插件供）。"""
        nonlocal md_status_was
        md_status = md_gw.connected
        if md_status and not md_status_was:
            sm.on_reconnect_edge()
        md_status_was = md_status

    def _lease_renew() -> None:
        """租约续期（Lua CAS）：续不上=被抢占/丢失 → 让位退出（exit 5）；网络异常容忍一轮。

        M5：前置查 intent——见切换意图则不再续租（交由 _intent_poll 让位）。
        """
        intent = _read_intent(r)
        if intent is not None and intent.get("target", "") != instance_name:
            return   # 被切走者：不再续租，交由 _intent_poll 让位（target==自己仍续租，防泄漏 exit 5）
        try:
            renewed = r.eval(_LEASE_RENEW_LUA, 1, LEASE_KEY, my_uuid, "30")
            if not int(renewed):
                logger.critical("租约续期失败（被抢占或丢失），退出")
                _alert("行情 hub 租约丢失，实例退出", "另一实例在位或存储异常；systemd 将接管。", code="hub.lease-lost")
                os._exit(5)
        except SystemExit:
            raise
        except Exception as e:
            logger.error("租约续期异常（容忍一轮）: %s", e)

    def _intent_poll() -> None:
        """M5：轮询切换意图。见 intent 且 target≠自己 → CAS DEL lease → exit(0)（优雅让位）。"""
        intent = _read_intent(r)
        if intent is None:
            return
        target = intent.get("target", "")
        if target == instance_name:
            return   # 我是切换目标，boot 已用 guarded 接管，无需让位
        logger.info("见切换意图 target=%s（本=%s），优雅让位 exit 0", target, instance_name)
        _lease_release(r, my_uuid)   # CAS DEL lease（==my_uuid 才删，防删 B 已拿到的 lease）
        os._exit(0)

    # 三窗分窗 finalize（P2 修复批 08-28 替代 flush_all 双点；盲审 B-P1-1 加宽 5~30s：
    # 钩子实际间隔 5s+δ>原窗宽 5s，straddle 相位会整窗 miss——pop 语义幂等，宽窗安全）：
    #   11:30 窗收 11:29 桶 / 15:00 窗收 14:59 桶 / 15:01 窗收 15:00 桶（竞价快照聚齐后，
    #   原半熟落库 V=0 即双轨四分类③）
    flush_slots = {1130: 11 * 60 + 29, 1500: 14 * 60 + 59, 1501: 15 * 60}

    def _flush() -> None:
        now = datetime.now()
        hm = now.hour * 100 + now.minute
        slot = flush_slots.get(hm)
        if slot is not None and 5 <= now.second < 30:
            for bar in agg.flush_minute(slot):
                _publish(bar)
            if hm == 1501:
                # 日终兜底（代码盲审 A-P2-b）：收当日一切滞留桶（断流标的尾根），防次日丢根
                for bar in agg.flush_rest():
                    _publish(bar)

    hb = HeartbeatWriter(r, HB_KEY, ttl=90)   # R-OBS1；超集原则：旧字段名一字不改，只增 ts

    def _heartbeat() -> None:
        hb.beat(pid=os.getpid(), gen=gen, subs=len(sm.current),
                ticks=stats["ticks"], bars=stats["bars"], sess_ticks=counters.sess_count,
                last_tick_ts=stats["last_tick_wall"] or 0, dropped_pg=pgw.dropped)

    loop = EngineLoop(name="md-hub", step=5.0,
                      watchdog=lambda: _sd_notify("WATCHDOG=1"),   # systemd 看门狗喂狗
                      event_engines=md_gw.event_engines,            # 事件线程存活（R-BR12，死→exit 1；批 63 二：网关插件供）
                      on_fatal=lambda reason: _alert(f"行情 hub {reason}，自动重启",
                                                     "实例退出由 systemd 接管；请查 journalctl 定位首个异常。",
                                                     code="runtime.fatal"),
                      fatal_exit_code=1)
    loop.every("lease-renew", 5.0, _lease_renew)    # 租约 30s TTL，5s 一续（失败 exit 5 在钩子内自带）
    loop.every("intent-poll", 5.0, _intent_poll)    # M5：轮询切换意图（见则优雅让位 exit 0）
    loop.every("md-edge", 0.0, _md_edge)            # 重连沿检测：每步
    loop.every("subs-poll", 15.0, sm.poll)          # 订阅 diff（旧 counter%3 = 15s）
    loop.every("subs-replay", 60.0, sm.replay)      # 全量幂等重放（旧 %60<10 窗口法 = 60s）
    loop.every("flush", 5.0, _flush)                # 三窗分窗 finalize（P2 修复批 08-28）
    loop.every("heartbeat", 5.0, _heartbeat)        # 心跳（R-OBS1）
    loop.every("md-supervise", 0.0,                # 会话监督：每步（批 63 二收编网关内——XTP=L2 五段续航/重登/告警）
               # 批 4b D2：交易日按日缓存下沉 md_session.is_trading_day 本体（等值消重：
               # schedule_due 内部裸打 DB 一并消掉）
               lambda: md_gw.poll_supervise(in_session=_in_astock_session(), trading_day=is_trading_day()))
    try:
        loop.run()   # 永续（到期驱动；进程域退出在钩子/骨架内 os._exit 带码）
    except KeyboardInterrupt:
        pass
    finally:
        os._exit(0)   # 原生库拆除规避（同 runner）；SystemExit 路径已直接 os._exit 带码


if __name__ == "__main__":
    main()
