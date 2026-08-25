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

from src.md_hub.parts import (   # 数据面部件（批 2 原样移驻；import 即重导出保测试路径）
    LATEST_TICK_PREFIX,
    LEASE_KEY,
    MinuteAggregator,
    ThinGateway,
    _LEASE_RENEW_LUA,
    _PGWriter,
    _lease_boot,
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


def main() -> None:
    # #48：启动时列级校验（hub 侧同款）
    try:
        from src.data_platform.db import verify_schema
        from src.health_monitor.monitor import report_schema_findings
        report_schema_findings(verify_schema())
    except Exception as e:
        logger.warning("schema 校验异常（不阻断启动）: %s", e)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if EventEngine is None:
        logger.error("vnpy 未安装")
        raise SystemExit(1)

    r = make_valkey()

    # ——— 租约/代次（先拿权再连行情；重试/让位/退出语义在 parts._lease_boot）———
    my_uuid, gen = _lease_boot(r)
    logger.info("hub 启动：uuid=%s gen=%d", my_uuid, gen)

    # ——— 行情接入（ThinGateway + MdApi，零 TD）———
    from src.strategy_framework.broker import build_xtp_setting as _build_xtp_setting
    from src.strategy_framework.md_api_guard import GuardedXtpMdApi, SdkState
    from src.strategy_framework.md_session import XtpMdSession, is_trading_day
    from src.strategy_framework.runtime.loop import EngineLoop
    from src.strategy_framework.runtime.mdlink import MdSessionSupervisor
    from src.strategy_framework.runtime.pulse import HeartbeatWriter, SessionCounters
    from src.strategy_framework.runtime.subs import SubscriptionManager

    ee = EventEngine()
    ee.start()   # 绕开 MainEngine 必须自启（构造不启动，_active=False → 线程未活）
    gw = ThinGateway(ee, "XTP")
    md_api = GuardedXtpMdApi(gw)   # 批1：SDK 生命周期守卫（SEGV 结构性绝迹）
    gw.md_api = md_api
    # L2 会话（韧性分层模型 2026-08-24）：定时续航+反应式重登，批 2 起由 MdSessionSupervisor 在主循环节拍内驱动
    md_sess = XtpMdSession(md_api)

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
    def on_tick(event):
        tick = event.data
        symbol = _project_symbol(tick)
        stats["ticks"] += 1
        stats["last_tick_wall"] = time.time()
        if _in_astock_session():
            # 只在盘中喂 on_data：旧 sess_last_tick 仅盘中写入——盘外回放不建断流基线，
            # supervisor 的断流症状/告警不会在盘外（夜间回放停止/假日静默）误触（行为值不变铁律）
            counters.on_data(True)
        _write_latest_tick(r, symbol, tick, _lt_fail_ts)
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
                _alert("hub XADD 失败（bar 丢失）", f"{bar['symbol']} {bar['ts']}")
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

    from vnpy.trader.event import EVENT_TICK
    ee.register(EVENT_TICK, on_tick)

    # MD 生命周期可见化（2026-08-24 僵尸会话事件）：连接/断开/重登日志走 EVENT_LOG，
    # 此前只注册 EVENT_TICK 全被丢弃 -- hub 侧会话状态完全不可观测，诊断只能靠猜
    from vnpy.trader.event import EVENT_LOG

    @_guard("hub.on_log")
    def on_log(event):
        logger.info("[gw] %s", getattr(event.data, "msg", event.data))
    ee.register(EVENT_LOG, on_log)

    # ——— 连接 + 订阅（真相源=DB，15s diff + 60s 幂等重放，R-SUB）———
    setting = _build_xtp_setting()
    md_api.connect(setting["账号"], setting["密码"], int(setting["客户号"]),
                   setting["行情地址"], int(setting["行情端口"]), setting.get("行情协议", "TCP"), 3)

    from vnpy.trader.object import SubscribeRequest
    from vnpy.trader.constant import Exchange
    _EX = {"SHSE": Exchange.SSE, "SZSE": Exchange.SZSE, "BSE": getattr(Exchange, "BSE", Exchange.SSE)}
    md_status_was = False   # MD 重连沿基态（SA2 hub 版）

    def _desired_symbols() -> set[str]:
        """订阅真相源（四源）：running 任务标的 ∪ system_config 白名单 ∪ 池标的 ∪ 临时订阅。

        池源（2026-08-19 分钟数据策略：XTP hub 自攒为主）：所有配置了 minute_history_start
        的池的成员自动进订阅——即使没有 running 任务在该标的上，hub 也会为它积累分钟 bar。
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
                # 池源：minute_history_start 非空的池的成员（XTP 自攒分钟数据）
                try:
                    cur = conn.execute(
                        "SELECT DISTINCT ps.symbol FROM pool_symbols ps "
                        "JOIN pools p ON p.id = ps.pool_id "
                        "WHERE p.minute_history_start IS NOT NULL")
                    pool_rows = {x[0] for x in cur.fetchall() if x[0]}
                    if pool_rows:
                        rows |= pool_rows
                        # 2026-08-23 降噪：15s 轮询每轮都打=噪音；实际订阅变化由
                        # 订阅同步的「订阅同步：+N -M」日志呈现，轮询内部态降 debug
                        logger.debug("池源订阅 +%d 标的", len(pool_rows))
                except Exception as e:
                    logger.warning("读池订阅源失败: %s", e)
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
            raw, ex = sym.rsplit(".", 1)
            e = _EX.get(ex)
            if e:
                md_api.subscribe(SubscribeRequest(symbol=raw, exchange=e))
        except Exception as e:
            logger.warning("订阅失败 %s: %s", sym, e)

    def _unsubscribe(sym: str) -> None:
        """退订（2026-08-20 生命周期闭环：出池/临时订阅过期/白名单摘除/live_task 停）。

        先 flush 在桶分钟防丢最后一根，再 SDK 原生退订——原订阅同步只加不减，
        移除标的的 tick 白收（带宽/CPU+latest_tick 键残留到 TTL）。
        补盲审 G1：EXCHANGE_VT2XTP 无 BSE 键——.get() 取 None 静默跳过（与 _subscribe 对称，
        BSE 端到端本就不通，防 KeyError 噪音刷 warning）。
        双盲审 P2：unSubscribeMarketData 前判 LOGGED_IN 态——非登录态（SDK 断线/
        重登窗口）裸调 C 面有炸回调线程风险，跳过+debug（与守卫 subscribe 软防护
        对称）。注意：跳过后订阅账本仍按 want 记账，被跳过的退订对下轮 diff 不可见——
        真实兜底是重连/重登后的全量重放（服务端订阅清零，以 desired 重建），非 diff。
        """
        try:
            bar = agg.flush_symbol(sym)
            if bar:
                _publish(bar)
            raw, ex = sym.rsplit(".", 1)
            e = _EX.get(ex)
            from vnpy_xtp.gateway.xtp_gateway import EXCHANGE_VT2XTP
            xtp_ex = EXCHANGE_VT2XTP.get(e) if e else None
            if xtp_ex is None:
                return
            if md_api.state is not SdkState.LOGGED_IN:
                logger.debug("退订跳过 %s（MD 态 %s 非登录态，待重连后 diff 补齐）",
                             sym, md_api.state.value)
                return
            md_api.unSubscribeMarketData(raw, 1, xtp_ex)
            logger.info("退订 %s（生命周期结束）", sym)
        except Exception as e:
            logger.warning("退订失败 %s: %s", sym, e)

    # 订阅管理收编骨架（批 2）：旧 _sync_subscriptions 的 diff/全量重放/重连沿/退订 flush
    # 语义原样（SubscriptionManager），节奏由下方钩子注册——15s diff / 60s 全量重放
    # （替换 %60<10 窗口法：同效果，无相位耦合）
    sm = SubscriptionManager(desired=_desired_symbols, subscribe=_subscribe, unsubscribe=_unsubscribe)
    sm.replay()   # 启动全量订阅（旧 _sync_subscriptions(force=True)）
    logger.info("hub 就绪，初始订阅 %d", len(sm.current))

    # ——— 主循环（批 2：EngineLoop 到期驱动；喂狗/事件线程存活检查内建骨架）———
    def _md_edge() -> None:
        """MD 重连沿：connect_status 上升沿 → 强制全量重放（XTP 重连不恢复订阅）。"""
        nonlocal md_status_was
        md_status = bool(getattr(md_api, "connect_status", True))
        if md_status and not md_status_was:
            sm.on_reconnect_edge()
        md_status_was = md_status

    def _lease_renew() -> None:
        """租约续期（Lua CAS）：续不上=被抢占/丢失 → 让位退出（exit 5）；网络异常容忍一轮。"""
        try:
            renewed = r.eval(_LEASE_RENEW_LUA, 1, LEASE_KEY, my_uuid, "30")
            if not int(renewed):
                logger.critical("租约续期失败（被抢占或丢失），退出")
                _alert("行情 hub 租约丢失，实例退出", "另一实例在位或存储异常；systemd 将接管。")
                os._exit(5)
        except SystemExit:
            raise
        except Exception as e:
            logger.error("租约续期异常（容忍一轮）: %s", e)

    flush_points = {1130, 1500}   # 11:30:05 / 15:00:05 双 flush（评审 S2）

    def _flush() -> None:
        """双 flush 窗口（分钟末后 5s，评审 S7 避开 :00-:04 进 tick 窗口；步长 5s 必命中）。"""
        now = datetime.now()
        if now.hour * 100 + now.minute in flush_points and 5 <= now.second < 10:
            for bar in agg.flush_all():
                _publish(bar)

    hb = HeartbeatWriter(r, HB_KEY, ttl=90)   # R-OBS1；超集原则：旧字段名一字不改，只增 ts

    def _heartbeat() -> None:
        hb.beat(pid=os.getpid(), gen=gen, subs=len(sm.current),
                ticks=stats["ticks"], bars=stats["bars"], sess_ticks=counters.sess_count,
                last_tick_ts=stats["last_tick_wall"] or 0, dropped_pg=pgw.dropped)

    # L2 监督器（韧性分层模型）：定时续航/反应式重登/恢复/零tick告警/断流告警五段内聚；
    # 默认 AlertPolicy=hub 现值（600/300/150/30/60），告警文案与老 hub 逐字对齐
    sup = MdSessionSupervisor(md_sess, counters, _alert, role="hub",
                              context=lambda: f"订阅 {len(sm.current)} 个标的。")

    _td_cache: dict = {"d": None, "v": True}

    def _trading_day() -> bool:
        """交易日（按日缓存——is_trading_day 走 DB 查 trade_cal，不能每步打）。"""
        d = datetime.now().date()
        if _td_cache["d"] != d:
            _td_cache["d"], _td_cache["v"] = d, is_trading_day()
        return _td_cache["v"]

    loop = EngineLoop(name="md-hub", step=5.0,
                      watchdog=lambda: _sd_notify("WATCHDOG=1"),   # systemd 看门狗喂狗
                      event_engines=(ee,),                          # 事件线程存活（R-BR12，死→exit 1）
                      on_fatal=lambda reason: _alert(f"行情 hub {reason}，自动重启",
                                                     "实例退出由 systemd 接管；请查 journalctl 定位首个异常。"),
                      fatal_exit_code=1)
    loop.every("lease-renew", 5.0, _lease_renew)    # 租约 30s TTL，5s 一续（失败 exit 5 在钩子内自带）
    loop.every("md-edge", 0.0, _md_edge)            # 重连沿检测：每步
    loop.every("subs-poll", 15.0, sm.poll)          # 订阅 diff（旧 counter%3 = 15s）
    loop.every("subs-replay", 60.0, sm.replay)      # 全量幂等重放（旧 %60<10 窗口法 = 60s）
    loop.every("flush", 5.0, _flush)                # 双 flush 窗口判断（逻辑原样保留）
    loop.every("heartbeat", 5.0, _heartbeat)        # 心跳（R-OBS1）
    loop.every("l2-supervise", 0.0,                 # L2 会话自愈：每步
               lambda: sup.tick(in_session=_in_astock_session(), trading_day=_trading_day()))
    try:
        loop.run()   # 永续（到期驱动；进程域退出在钩子/骨架内 os._exit 带码）
    except KeyboardInterrupt:
        pass
    finally:
        os._exit(0)   # 原生库拆除规避（同 runner）；SystemExit 路径已直接 os._exit 带码


if __name__ == "__main__":
    main()
