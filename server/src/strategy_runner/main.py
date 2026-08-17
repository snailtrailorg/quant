"""策略实盘化入口（#4 修正版 B）。

每策略独立子进程：
  strategy_config -> MainEngine + XtpGateway + Strategy + XTPAdapter -> 启动 -> 保持运行

启动: python -m src.strategy_runner.main --id <strategy_id>
systemd: systemctl start quant-strategy@<id>
"""
import argparse
import logging
import os
import sys
import time

logger = logging.getLogger("strategy_runner")

try:
    from vnpy.event import EventEngine
    from vnpy.trader.engine import MainEngine
except ImportError:
    MainEngine = None
    EventEngine = None


def _build_xtp_setting() -> dict:
    """组装 vnpy XtpGateway SETTING（中文 key）。优先 Broker DB（PI3），fallback .env XTP_TEST_*（开发期）。

    Broker DB: credentials_encrypted={app_id,app_secret,client_id,auth_code} + params={td_host,td_port,md_host,md_port}
    .env: XTP_TEST_ACCOUNT/PASSWORD/CLIENT_ID/QUOTE_HOST/QUOTE_PORT/TRADE_HOST/TRADE_PORT/KEY
    """
    # 优先 Broker DB
    try:
        from src.strategy_framework.broker import get_broker
        broker = get_broker("xtp")
        if broker:
            cred = broker.get_credentials()
            params = broker._params or {}
            if cred.get("app_id"):
                return {
                    "账号": cred.get("app_id", ""),
                    "密码": cred.get("app_secret", ""),
                    "客户号": int(cred.get("client_id", params.get("client_id", 1)) or 1),
                    "行情地址": params.get("md_host", ""),
                    "行情端口": int(params.get("md_port", 0) or 0),
                    "交易地址": params.get("td_host", ""),
                    "交易端口": int(params.get("td_port", 0) or 0),
                    "行情协议": "TCP",
                    "授权码": cred.get("auth_code", ""),
                    "日志级别": "INFO",
                }
    except Exception as e:
        logger.warning("Broker DB 取 XTP 凭证失败，fallback .env: %s", e)

    # fallback .env（开发期 broker_config 未配）
    import os
    from dotenv import load_dotenv
    load_dotenv()
    return {
        "账号": os.environ.get("XTP_TEST_ACCOUNT", ""),
        "密码": os.environ.get("XTP_TEST_PASSWORD", ""),
        "客户号": int(os.environ.get("XTP_TEST_CLIENT_ID", "1")),
        "行情地址": os.environ.get("XTP_TEST_QUOTE_HOST", ""),
        "行情端口": int(os.environ.get("XTP_TEST_QUOTE_PORT", "0") or 0),
        "交易地址": os.environ.get("XTP_TEST_TRADE_HOST", ""),
        "交易端口": int(os.environ.get("XTP_TEST_TRADE_PORT", "0") or 0),
        "行情协议": "TCP",
        "授权码": os.environ.get("XTP_TEST_KEY", ""),
        "日志级别": "INFO",
    }


def _warmup_history(symbol: str) -> list:
    """PG 暖机：读历史 bar 填充 history（因子初始化 / 断线补缺口，#4）。返回 list。"""
    history = []
    try:
        from src.data_platform.db import get_bars
        from datetime import datetime as _dt, timedelta
        bars_df = get_bars(symbol, "1min", _dt.now() - timedelta(days=30), _dt.now())
        if not bars_df.empty:
            for _, row in bars_df.tail(100).iterrows():
                history.append({
                    "ts": row["ts"],
                    "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]),
                    "volume": float(row["volume"]) if row["volume"] else 0,
                })
            logger.info("PG 暖机: 读 %d 根历史 bar", len(history))
    except Exception as e:
        logger.warning("PG 暖机失败（因子首次可能不准）: %s", e)
    return history


# ——— SA 稳定性加固（2026-08-17 稳定性检查 SA1/SA2，F-26/F-24/F-25/F-18）———

def _guard(name: str):
    """handler 包装：任何异常（用户策略代码/PG/风控 KeyError 等）只记日志不上抛。

    vnpy EventEngine 事件线程对 handler 异常零保护（engine.py 只捕 Empty），一次异常=线程
    静默死亡=永久失聪（F-26）。本包装把"失聪"降级为"跳过本条事件+告警日志"。
    """
    def deco(fn):
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.exception("handler %s 异常（已拦截，事件线程存活）", name)
                try:
                    _alert(f"策略任务 handler 异常: {name}", "事件已跳过，策略继续运行。详见 journalctl。")
                except Exception:
                    pass  # 守卫绝不放行任何异常（纵深防御）
        return wrapped
    return deco


def _in_astock_session(now=None) -> bool:
    """A 股交易时段（周一~周五 9:31-11:30 / 13:01-15:00）。

    节假日不感知——调用方必须叠加"今日已收到过 tick"条件，防止假日误判断流。
    """
    import datetime as _dt
    now = now or _dt.datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return (931 <= hm <= 1130) or (1301 <= hm <= 1500)


def _sd_notify(msg: str) -> None:
    """systemd notify（喂 WATCHDOG 看门狗）。无 NOTIFY_SOCKET（本地/手工运行）时静默跳过。"""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        import socket
        if addr.startswith("@"):
            addr = "\0" + addr[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
    except Exception:
        pass  # 喂狗失败不杀主流程（systemd 会重启，靠 Restart 兜底）


def _alert(title: str, body: str = "") -> None:
    """runner 侧告警：走通知中心（站内铃铛+外部推送），失败仅记日志绝不影响交易主流程。"""
    try:
        from src.alert_notify.notify import notify
        notify("critical", "system", title, body)
    except Exception as e:
        logger.warning("告警发送失败（%s）: %s", title, e)


def main():
    parser = argparse.ArgumentParser(description="策略实盘化入口")
    parser.add_argument("--task-id", help="live_task.id（新架构：策略与标的分离）")
    parser.add_argument("--id", help="strategy_config.id（旧架构兼容）")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if MainEngine is None:
        logger.error("vnpy 未安装，策略实盘需要 vnpy 环境")
        sys.exit(1)

    if not args.task_id and not args.id:
        logger.error("必须提供 --task-id（新）或 --id（旧）")
        sys.exit(1)

    # 1. 读 live_task（新架构）或 strategy_config（旧架构兼容）
    from src.data_platform.db import get_conn
    import json as _json

    if args.task_id:
        # 新架构：策略与标的分离
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id, name, strategy_id, symbol, params, strategy_snapshot, "
                "status, account_id, initial_capital FROM live_task WHERE id=%s",
                (args.task_id,))
            row = cur.fetchone()
        if not row:
            logger.error("实盘任务 %s 不存在", args.task_id)
            sys.exit(1)
        tid, task_name, strategy_id, symbol, task_params_raw, snapshot_raw, status, account_id, initial_capital = row
        if status == "stopped":
            logger.info("实盘任务 %s 已停止，退出", tid)
            sys.exit(0)
        task_params = _json.loads(task_params_raw) if isinstance(task_params_raw, str) else (task_params_raw or {})
        snapshot = _json.loads(snapshot_raw) if isinstance(snapshot_raw, str) else (snapshot_raw or {})
        # 从快照构建 StrategyConfig 参数
        sid = snapshot.get("id", strategy_id)
        name = snapshot.get("name", task_name)
        s_type = snapshot.get("type", "astock_analysis")
        adapter_type = snapshot.get("adapter", "xtp")
        factors = snapshot.get("factors", [])
        aggregator = snapshot.get("aggregator", {})
        # params：策略快照的 params（含 mode/python_code）+ 任务级参数覆盖
        base_params = snapshot.get("params", {})
        # 任务级参数覆盖策略级（mode/python_code 等保留策略级，数值参数用任务级）
        params = {**base_params, **task_params}
        logger.info("实盘任务 %s (策略 %s, 标的 %s) 启动", tid, sid, symbol)
    else:
        # 旧架构兼容：从 strategy_config 读
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id, name, type, symbol, adapter, enabled, factors, "
                "aggregator, params, backtest_verified FROM strategy_config WHERE id=%s",
                (args.id,))
            row = cur.fetchone()
        if not row:
            logger.error("策略 %s 不存在", args.id)
            sys.exit(1)
        sid, name, s_type, symbol, adapter_type, enabled, factors, aggregator, params, bt_verified = row
        factors = _json.loads(factors) if isinstance(factors, str) else (factors or [])
        aggregator = _json.loads(aggregator) if isinstance(aggregator, str) else (aggregator or {})
        params = _json.loads(params) if isinstance(params, str) else (params or {})
        if not enabled or not bt_verified:
            logger.warning("策略 %s 未启用或未回测验证，跳过", sid)
            sys.exit(0)
        tid = None
        account_id = None
        initial_capital = 1000000
        # 旧架构读 strategy_account
        try:
            with get_conn() as conn:
                cur = conn.execute("SELECT account_id, broker_provider, initial_capital FROM strategy_account WHERE strategy_id=%s LIMIT 1", (sid,))
                sa = cur.fetchone()
            if sa:
                initial_capital = float(sa[2]) if sa[2] else 1000000
                account_id = sa[0]
                logger.info("策略 %s 绑定账户 %s (%s, 资金 %s)", sid, sa[0], sa[1], initial_capital)
        except Exception as e:
            logger.warning("读 strategy_account 失败（用默认资金）: %s", e)

    # 2. 建 MainEngine + XtpGateway
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)

    try:
        from vnpy_xtp import XtpGateway

        gateway = main_engine.add_gateway(XtpGateway, "XTP")
    except Exception as e:
        logger.error("XtpGateway 加载失败: %s", e)
        main_engine.close()
        sys.exit(1)

    # 3. 建策略实例
    from src.strategy_framework.strategy import Strategy, StrategyConfig
    from src.strategy_framework.adapters import XTPAdapter

    adapter = XTPAdapter(gateway=gateway, event_engine=event_engine)
    cfg = StrategyConfig(
        id=sid, name=name, type=s_type, symbol=symbol, adapter=adapter_type,
        enabled=True, factors=factors or [], aggregator=aggregator or {},
        params=params or {},
    )
    strategy = Strategy.from_config(cfg, adapter)

    # 4. 从 Broker DB 取凭证（PI3）+ connect
    setting = _build_xtp_setting()
    if not setting.get("账号") or not setting.get("交易地址"):
        logger.error("XTP 凭证不完整（broker_config 无 xtp 记录，且 .env XTP_TEST_* 未配）")
        sys.exit(1)
    gateway.connect(setting)

    # 5. 行情驱动：EVENT_TICK -> BarGenerator -> strategy.on_bar（#4 核心）
    from vnpy.trader.event import EVENT_TICK
    from vnpy.trader.utility import BarGenerator
    from vnpy.trader.object import SubscribeRequest
    from vnpy.trader.constant import Exchange
    from src.data_platform.schema import parse_vt_symbol

    history: list[dict] = _warmup_history(symbol)  # 历史 bar（暖机/因子窗口，#4）

    def _bar_to_dict(bar):
        return {
            "ts": bar.datetime,
            "open": float(bar.open_price), "high": float(bar.high_price),
            "low": float(bar.low_price), "close": float(bar.close_price),
            "volume": float(bar.volume) if bar.volume else 0,
        }

    @_guard("on_bar")
    def on_vnpy_bar(bar):
        d = _bar_to_dict(bar)
        sig = strategy.on_bar(d, list(history))  # history 不含当前（防未来）
        sig_action = getattr(sig, 'action', None)
        sig_name = sig_action.name if sig_action else "NONE"
        logger.info("BAR %s close=%.2f vol=%.0f signal=%s",
                     d.get("ts", "?").strftime("%H:%M") if hasattr(d.get("ts"), "strftime") else d.get("ts", "?"),
                     d.get("close", 0), d.get("volume", 0),
                     sig_name)
        history.append(d)
        if len(history) > 100:
            history.pop(0)

    bg = BarGenerator(on_vnpy_bar)

    _tick_state = {"last_ts": 0.0, "count": 0}  # GIL 下原子读写，无需锁

    @_guard("on_tick")
    def on_tick(event):
        _tick_state["last_ts"] = time.time()
        _tick_state["count"] += 1
        bg.update_tick(event.data)

    event_engine.register(EVENT_TICK, on_tick)

    # 订阅 symbol（vt_symbol SHSE -> vnpy Exchange SSE 映射）
    raw, ex = parse_vt_symbol(symbol)
    ex_vnpy = {"SHSE": "SSE", "SZSE": "SZSE", "BSE": "BSE"}.get(ex, ex)
    exchange = getattr(Exchange, ex_vnpy, None)
    if not exchange:
        logger.error("无法解析交易所: %s（vt_symbol=%s）", ex, symbol)
        sys.exit(1)
    sub_req = SubscribeRequest(symbol=raw, exchange=exchange)

    def _resubscribe():
        """幂等重订阅。XTP 断线重连后不恢复订阅（F-25），周期性重放是兜底（F-24 启动竞态同治）。"""
        try:
            main_engine.subscribe(sub_req, "XTP")
        except Exception as e:
            logger.warning("重订阅失败: %s", e)

    _resubscribe()
    logger.info("策略 %s (%s) 启动，订阅 %s", sid, name, symbol)

    # 6. 保持运行（事件循环）
    import os as _os, redis as _redis
    _r = _redis.Redis.from_url(_os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    counter = 0
    _halt_state = {"was": False}  # 熔断沿检测（SB2，F-41：进入熔断的瞬间撤在场单）
    try:
        while True:
            time.sleep(10)
            counter += 1
            # P4-3 停止条件热检查（每 60s）：
            # 新架构查自己的 live_task.status（stop_live_task 置 stopped）；
            # 旧架构查 strategy_config.enabled。2026-08-17 踩坑：新架构误查旧架构字段，
            # 策略未 enable 的任务每 60s 自杀重启，history 永远攒不满出不了信号。
            if counter % 6 == 0:
                try:
                    with get_conn() as conn:
                        if tid is not None:
                            cur = conn.execute('SELECT status FROM live_task WHERE id=%s', (tid,))
                            r = cur.fetchone()
                            if r and r[0] == 'stopped':
                                logger.info('实盘任务 %s 被 Web 停止，退出', tid)
                                break
                        else:
                            cur = conn.execute('SELECT enabled FROM strategy_config WHERE id=%s', (sid,))
                            r = cur.fetchone()
                            if r and not r[0]:
                                logger.info('策略 %s 被 Web 停止，退出', sid)
                                break
                except Exception as e:
                    logger.warning("停止条件检查失败: %s", e)
            # 因子重算触发（#31，data_continuity_check 补采后设标记 -> 重填 history）
            try:
                if _r.get("factor:recalc:triggered"):
                    history[:] = _warmup_history(symbol)
                    _r.delete("factor:recalc:triggered")
                    logger.info("因子重算触发：重填 %d 根历史 bar", len(history))
            except Exception as e:
                logger.warning("因子重算触发检查失败: %s", e)
            # ——— SA 加固主循环（每 10s 一轮）———
            # 1) 喂 systemd 看门狗（WatchdogSec 由 unit 配置，挂死→systemd 重启）
            _sd_notify("WATCHDOG=1")
            # 2) EventEngine 线程存活（F-26）：死了≠进程死，必须显式检测；告警后退出交给 systemd 拉起
            _ev_thread = getattr(event_engine, "_thread", None)
            if _ev_thread is not None and not _ev_thread.is_alive():
                logger.critical("EventEngine 事件线程已死亡，退出待 systemd 重启")
                _alert(f"实盘任务事件线程死亡: {sid}", "runner 将自动重启；请查 journalctl 定位首个异常")
                os._exit(1)
            # 3) tick 新鲜度（F-18/F-25）：交易时段断流检测。
            #    仅当今日已收到过 tick 才升级到退出（节假日/盘前零 tick 安全）；
            #    120s 告警，300s 告警+退出重启（也覆盖事件线程挂死但未死、订阅丢失等一切断流形态）
            _stale = time.time() - _tick_state["last_ts"] if _tick_state["last_ts"] else None
            if _in_astock_session():
                if _tick_state["count"] > 0 and _stale is not None and _stale > 120:
                    if _stale > 300:
                        logger.critical("tick 断流 %.0fs（>300s），退出待 systemd 重启恢复订阅", _stale)
                        _alert(f"实盘任务 tick 断流 {_stale:.0f}s，自动重启恢复: {sid}",
                               "重启后自动重放订阅并暖机。若反复出现请查 XTP 行情链路。")
                        os._exit(1)
                    elif counter % 6 == 0:  # 告警限频：每 60s 至多一条
                        logger.error("tick 断流 %.0fs（今日已收 %d 条）", _stale, _tick_state["count"])
                        _alert(f"实盘任务 tick 断流 {_stale:.0f}s: {sid}",
                               f"今日已收 {_tick_state['count']} 条 tick 后断流，120-300s 内未恢复将自动重启。")
                # 4) 幂等重订阅（F-24/F-25）：交易时段每 60s 重放一次，兜住"重连后订阅丢失"
                if counter % 6 == 0:
                    _resubscribe()
            # 5) Valkey 心跳（供巡检/看板；失联只记日志）
            try:
                _r.hset(f"quant:hb:task:{tid or sid}", mapping={
                    "pid": os.getpid(), "ts": time.time(),
                    "last_tick_ts": _tick_state["last_ts"] or 0,
                    "ticks": _tick_state["count"], "bars": len(history),
                })
                _r.expire(f"quant:hb:task:{tid or sid}", 90)
            except Exception as e:
                logger.warning("写心跳失败: %s", e)
            # 6) 熔断沿触发：撤销全部在场委托（SB2，F-41——熔断只拦新单不撤旧单=熔断期间仍建仓）
            try:
                from src.risk_control.risk import RiskControl
                halted_now = RiskControl.get().is_halted()
            except Exception:
                halted_now = _halt_state["was"]  # Valkey 不可达时保持上一状态（check_order 侧已保守拒单）
            if halted_now and not _halt_state["was"]:
                logger.critical("检测到熔断，撤销全部在场委托")
                _alert(f"熔断触发，已自动撤销在场委托: {sid}", "check_order 已拒新单；在场委托撤销结果见 journalctl。")
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
            _halt_state["was"] = halted_now
            # 定期写 account_snapshot（#6，每 60s query_account -> PG）
            if counter % 6 == 0:
                try:
                    accounts = adapter.query_account() or []
                    if not accounts:
                        # SB1（F-34）：查不到账户（TD 断线/查询超时）绝不写假值——
                        # 旧逻辑把 initial_capital 当总资产写入，恰好把风控回撤"归零回正"
                        logger.warning("query_account 无结果（TD 断线？），跳过本轮快照（不写假值）")
                    else:
                        total = sum(float(getattr(a, "balance", 0)) for a in accounts)
                        # P3-10 daily_pnl = 今日首次快照基准的偏差
                        import datetime as _dt2
                        today_str = _dt2.datetime.now().strftime('%Y-%m-%d')
                        with get_conn() as conn:
                            cur = conn.execute("SELECT total_value FROM account_snapshot WHERE ts::date=%s ORDER BY ts ASC LIMIT 1", (today_str,))
                            first_row = cur.fetchone()
                            daily_base = float(first_row[0]) if first_row else total
                            daily_pnl = total - daily_base
                            conn.execute("INSERT INTO account_snapshot (total_value, daily_pnl, initial_capital) VALUES (%s, %s, %s)", (total, daily_pnl, initial_capital if initial_capital is not None else 1000000))
                            conn.commit()
                except Exception as e:
                    logger.warning("写 account_snapshot 失败: %s", e)
    except KeyboardInterrupt:
        logger.info("策略 %s 停止", sid)
    finally:
        try:
            _r.close()
            main_engine.close()
        except Exception:
            pass
    # XTP/vnpy 原生库在解释器拆除阶段会 abort（status=6/ABRT，2026-08-17 实测），
    # 清理完成后硬退出跳过原生 teardown——退出码干净，systemd 不误判失败重启。
    os._exit(0)


if __name__ == "__main__":
    main()