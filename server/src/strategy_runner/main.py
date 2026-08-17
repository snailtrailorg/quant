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

    def on_tick(event):
        bg.update_tick(event.data)

    event_engine.register(EVENT_TICK, on_tick)

    # 订阅 symbol（vt_symbol SHSE -> vnpy Exchange SSE 映射）
    raw, ex = parse_vt_symbol(symbol)
    ex_vnpy = {"SHSE": "SSE", "SZSE": "SZSE", "BSE": "BSE"}.get(ex, ex)
    exchange = getattr(Exchange, ex_vnpy, None)
    if exchange:
        main_engine.subscribe(SubscribeRequest(symbol=raw, exchange=exchange), "XTP")
        logger.info("策略 %s (%s) 启动，订阅 %s", sid, name, symbol)
    else:
        logger.error("无法解析交易所: %s（vt_symbol=%s）", ex, symbol)
        sys.exit(1)

    # 6. 保持运行（事件循环）
    import os as _os, redis as _redis
    _r = _redis.Redis.from_url(_os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    counter = 0
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
            # 心跳检查 + 断线重连
            if hasattr(gateway, "is_connected") and not gateway.is_connected():
                logger.warning("网关断连，尝试重连")
                try:
                    gateway.connect(setting)
                    history[:] = _warmup_history(symbol)  # 断线补缺口：重连后从 PG 重填（#4）
                    logger.info("断线补缺口：重填 %d 根历史 bar", len(history))
                except Exception as e:
                    logger.warning("重连失败: %s", e)
            # 定期写 account_snapshot（#6，每 60s query_account -> PG）
            if counter % 6 == 0:
                try:
                    accounts = adapter.query_account() or []
                    total = sum(float(getattr(a, "balance", 0)) for a in accounts) if accounts else initial_capital
                    # P3-10 daily_pnl = 今日首次快照基准的偏差
                    import datetime as _dt2
                    today_str = _dt2.datetime.now().strftime('%Y-%m-%d')
                    with get_conn() as conn:
                        conn.execute("SELECT 1 FROM account_snapshot LIMIT 1")
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