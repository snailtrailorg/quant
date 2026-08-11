"""策略实盘化入口（#4 修正版 B）。

每策略独立子进程：
  strategy_config -> MainEngine + XtpGateway + Strategy + XTPAdapter -> 启动 -> 保持运行

启动: python -m src.strategy_runner.main --id <strategy_id>
systemd: systemctl start quant-strategy@<id>
"""
import argparse
import logging
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
                    "客户号": int(cred.get("client_id", params.get("client_id", 1))),
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
    parser.add_argument("--id", required=True, help="strategy_config.id")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if MainEngine is None:
        logger.error("vnpy 未安装，策略实盘需要 vnpy 环境")
        sys.exit(1)

    # 1. 读策略配置
    from src.data_platform.db import get_conn

    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, name, type, symbol, adapter, enabled, factors, "
            "aggregator, params, backtest_verified "
            "FROM strategy_config WHERE id=%s",
            (args.id,),
        )
        row = cur.fetchone()
    if not row:
        logger.error("策略 %s 不存在", args.id)
        sys.exit(1)

    sid, name, s_type, symbol, adapter_type, enabled, factors, aggregator, params, bt_verified = row
    # DB TEXT 列存 JSON 字符串，需 json.loads（P3 修复：服务器 psycopg 不自动解析）
    import json as _json
    factors = _json.loads(factors) if isinstance(factors, str) else (factors or [])
    aggregator = _json.loads(aggregator) if isinstance(aggregator, str) else (aggregator or {})
    params = _json.loads(params) if isinstance(params, str) else (params or {})
    if not enabled or not bt_verified:
        logger.warning("策略 %s 未启用或未回测验证，跳过", sid)
        sys.exit(0)

    # 读 strategy_account 绑定账户（#27）
    initial_capital = 1000000
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT account_id, broker_provider, initial_capital FROM strategy_account WHERE strategy_id=%s LIMIT 1", (sid,))
            sa = cur.fetchone()
        if sa:
            initial_capital = float(sa[2]) if sa[2] else 1000000
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
        sys.exit(1)

    # 3. 建策略实例
    from src.strategy_framework.strategy import Strategy, StrategyConfig
    from src.strategy_framework.adapters import XTPAdapter

    adapter = XTPAdapter(gateway=gateway, event_engine=event_engine)
    cfg = StrategyConfig(
        id=sid, name=name, type=s_type, symbol=symbol, adapter=adapter_type,
        enabled=enabled, factors=factors or [], aggregator=aggregator or {},
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
        strategy.on_bar(d, list(history))  # history 不含当前（防未来）
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
            # P4-3 参数热加载：每 60s 重读 strategy_config，如果 enabled=false 则退出
            if counter % 6 == 0:
                try:
                    with get_conn() as conn:
                        cur = conn.execute('SELECT enabled FROM strategy_config WHERE id=%s', (sid,))
                        r = cur.fetchone()
                    if r and not r[0]:
                        logger.info('策略 %s 被 Web 停止，退出', sid)
                        break
                except Exception:
                    pass
            # 因子重算触发（#31，data_continuity_check 补采后设标记 -> 重填 history）
            try:
                if _r.get("factor:recalc:triggered"):
                    history[:] = _warmup_history(symbol)
                    _r.delete("factor:recalc:triggered")
                    logger.info("因子重算触发：重填 %d 根历史 bar", len(history))
            except Exception:
                pass
            # 心跳检查 + 断线重连
            if hasattr(gateway, "is_connected") and not gateway.is_connected():
                logger.warning("网关断连，尝试重连")
                gateway.connect(setting)
                history[:] = _warmup_history(symbol)  # 断线补缺口：重连后从 PG 重填（#4）
                logger.info("断线补缺口：重填 %d 根历史 bar", len(history))
            # 定期写 account_snapshot（#6，每 60s query_account -> PG）
            if counter % 6 == 0:
                try:
                    accounts = adapter.query_account() or []
                    total = sum(float(getattr(a, "balance", 0)) for a in accounts) if accounts else initial_capital
                    # P3-10 daily_pnl = 今日首次快照基准的偏差
                    import datetime as _dt2
                    today_str = _dt2.datetime.now().strftime('%Y-%m-%d')
                    with get_conn() as conn:
                        conn.execute("CREATE TABLE IF NOT EXISTS account_snapshot (id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(), total_value NUMERIC, daily_pnl NUMERIC DEFAULT 0, initial_capital NUMERIC)")
                        cur = conn.execute("SELECT total_value FROM account_snapshot WHERE ts::date=%s ORDER BY ts ASC LIMIT 1", (today_str,))
                        first_row = cur.fetchone()
                        daily_base = float(first_row[0]) if first_row else total
                        daily_pnl = total - daily_base
                        conn.execute("INSERT INTO account_snapshot (total_value, daily_pnl, initial_capital) VALUES (%s, %s, %s)", (total, daily_pnl, initial_capital))
                        conn.commit()
                except Exception as e:
                    logger.warning("写 account_snapshot 失败: %s", e)
    except KeyboardInterrupt:
        logger.info("策略 %s 停止", sid)
    finally:
        main_engine.close()


if __name__ == "__main__":
    main()