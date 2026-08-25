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


# 2026-08-19 模块归位：build_xtp_setting 搬 strategy_framework/broker（hub/runner 双消费方）；
# 此别名保 tests/scripts 旧 import 兼容
from src.strategy_framework.broker import build_xtp_setting as _build_xtp_setting, get_xtp_param
from src.strategy_framework.md_api_guard import GuardedXtpMdApi
from src.strategy_framework.md_session import XtpMdSession

# --- SA4 退出码分类（sysexits 惯例；单元 Restart=on-failure + RestartPreventExitStatus=78）---
EX_OK = 0          # 正常停止（任务 stopped/策略 disabled）--on-failure 不拉起（F-36 churn 根修）
EX_TEMPFAIL = 75   # 瞬态（依赖探活退避耗尽）--systemd 重启 + reconciler 接管
EX_CONFIG = 78     # 永久配置错误（任务/策略不存在、凭证缺失、symbol 解析失败）--不重启，Failed 告警人工


def _pg_alive() -> bool:
    """PG 探活（SA4 依赖探活的硬依赖项；Valkey 运行期已全路径容忍，不阻塞启动）。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _wait_for_deps(max_wait: float = 600.0) -> bool:
    """SA4 启动依赖探活 + 进程内指数退避（5->10->20->40->60s 封顶）。

    服务器重启序（PG 慢于 runner）旧逻辑首查即崩 -> 5 次/5min 打穿 StartLimit ->
    Failed 死透等人工。改：进程内等依赖恢复（期间喂 systemd watchdog 防 90s 误杀），
    耗尽（默认 10min）返回 False 由上层 EX_TEMPFAIL 退出--systemd RestartSec 后重试，
    Failed 后 reconciler 兜底。返回 True = 依赖就绪。
    """
    delays = [5, 10, 20, 40]
    waited = 0.0
    while True:
        if _pg_alive():
            if waited:
                logger.info("依赖探活：PG 恢复（等待 %.0fs）", waited)
            return True
        _sd_notify("WATCHDOG=1")   # 探活期间照常喂狗，防 WatchdogSec 误杀
        d = delays.pop(0) if delays else 60
        waited += d
        if waited >= max_wait:
            logger.error("依赖探活退避耗尽（PG %.0fs 不可达）", waited)
            return False
        logger.warning("PG 未就绪，%.0fs 后重试（已等 %.0fs）", d, waited)
        time.sleep(d)


def _warmup_history(symbol: str, n: int = 100) -> list:
    """PG 暖机：读历史 bar 填充 history（因子初始化 / 断线补缺口，#4）。返回 list。"""
    history = []
    try:
        from src.data_platform.db import get_bars
        from datetime import datetime as _dt, timedelta
        bars_df = get_bars(symbol, "1min", _dt.now() - timedelta(days=30), _dt.now())
        if not bars_df.empty:
            for _, row in bars_df.tail(min(n, 500)).iterrows():
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


_account_baseline: float | None = None


def _account_baseline_capital(total: float) -> float:
    """账户基线净值（#10 口径修正 2026-08-22）。

    account_snapshot.initial_capital 原写 live_task 配置资金（策略级，默认 100 万），
    而 total_value 是账户级真值（如测试账户 10 亿）--total_pnl 虚增 9.99 亿、风控回撤
    分母错配。改：基线=该账户首条快照 total_value（跟踪起点净值）；无历史（首次跟踪）
    以当前查询值为基线。进程内缓存（基线不随运行漂移）。
    """
    global _account_baseline
    if _account_baseline is None:
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute("SELECT total_value FROM account_snapshot ORDER BY ts ASC LIMIT 1")
                row = cur.fetchone()
            _account_baseline = float(row[0]) if row and row[0] else total
        except Exception as e:
            logger.warning("读账户基线净值失败（以当前值为基线）: %s", e)
            _account_baseline = total
    return _account_baseline


# 2026-08-19 模块归位：guard/sd_notify/session 来自 quant_common（本包禁止依赖告警层，
# alert 回调在此注入——safe_notify 收编三处重复 try/except notify 模式）
from src.quant_common.session import in_astock_session as _in_astock_session, session_edge
from src.quant_common.guard import guard as _guard_base, sd_notify as _sd_notify


def _alert(title: str, body: str = "") -> None:
    """runner 侧告警：never-raise（safe_notify），绝不影响交易主流程。"""
    from src.alert_notify.notify import safe_notify
    safe_notify("critical", title, body)


def _guard(name):
    """守卫+告警注入（quant_common.guard 是无告警纯版）。

    alert 用 lambda 晚绑定模块全局 _alert——测试 patch src.strategy_runner.main._alert
    对已装饰 handler 仍生效（P-S6 patch 语义；直接传 _alert 会在装饰期固化=假绿）。
    """
    return _guard_base(name, alert=lambda title, body="": _alert(title, body))


def _run_hub_mode(sid, tid, name, s_type, symbol, factors, aggregator, params, initial_capital,
                  account_id=None):
    """ST7 hub 模式 worker（设计 14 v2 §3）：TD-only 接入 + 流消费，SA/SB/SC 机制全复用。"""
    import datetime as _dt
    from src.data_platform.db import get_conn as _get_conn  # 评审 S2：stop_check 用
    from vnpy.event import EventEngine
    from vnpy.trader.gateway import BaseGateway
    from vnpy_xtp.gateway.xtp_gateway import XtpTdApi
    from src.strategy_framework.strategy import Strategy, StrategyConfig
    from src.strategy_framework.adapters import XTPAdapter
    from src.strategy_runner.hub_worker import run as hub_worker_run

    logger.info("任务 %s 以 hub 模式启动（策略 %s 标的 %s）", tid or sid, sid, symbol)
    setting = _build_xtp_setting()
    boot_epoch = int(time.time())   # 评审 S8：秒级 epoch（分钟级同分钟重启会撞 id）

    class ThinTdGateway(BaseGateway):
        """TD-only 壳：事件转发 + 抽象方法转发 td_api（零 MD 零合约表，R-BR1/R-CAP1）。"""

        def connect(self, s: dict) -> None:
            self.td_api.connect(s["账号"], s["密码"], int(s["客户号"]), s["交易地址"],
                                int(s["交易端口"]), s.get("授权码", ""), 3)

        def subscribe(self, req) -> None:  # hub 模式 worker 无行情
            pass

        def send_order(self, req) -> str:
            return self.td_api.send_order(req)

        def cancel_order(self, req) -> None:
            self.td_api.cancel_order(req)

        def query_account(self) -> None:
            self.td_api.query_account()

        def query_position(self) -> None:
            self.td_api.query_position()

        def close(self) -> None:
            try:
                if getattr(self.td_api, "connect_status", False):
                    self.td_api.exit()
            except Exception:
                pass

    ee = EventEngine()
    ee.start()   # 同 md_hub：绕开 MainEngine 必须自启
    gw = ThinTdGateway(ee, "XTP")
    td_api = XtpTdApi(gw)
    gw.td_api = td_api
    gw.connect(setting)   # 只连 TD（R-TD1：hub 零 TD，worker 零 MD）

    adapter = XTPAdapter(gateway=gw, event_engine=ee,
                         order_prefix=f"t{tid or sid}:e{boot_epoch}:")
    cfg = StrategyConfig(id=sid, name=name, type=s_type, symbol=symbol, adapter="xtp",
                         enabled=True, factors=factors or [], aggregator=aggregator or {}, params=params or {})
    strategy = Strategy.from_config(cfg, adapter)

    # 评审 C2：冻结的真实抓手——包 adapter.send_order（下单唯一咽喉，strategy.place_order 必经）。
    # S6 修订（2026-08-18）：两段判定——①sticky 冻结（untrusted/gap=数据污染事实）BUY 拒/SELL 放；
    # ②动态新鲜度（bar 停更/hub 心跳）在 send_order 时刻按事实判定（ctx["buy_ok"] 由 hub_worker.run
    # 注入），不再依赖后台定时器预计算的 frozen["now"]——日历/节奏预期从动作路径清零。
    frozen: dict = {"now": False, "sticky": False}
    _orig_send = adapter.send_order
    ctx: dict = {}   # hub_worker.run 的上下文（buy_ok 在 run 内注入）

    from src.strategy_runner.hub_worker import frozen_allows as _frozen_allows

    def _gated_send(order):
        if not _frozen_allows(order.action, frozen):
            logger.warning("sticky 冻结拒绝 BUY 委托: %s %s", order.symbol, order.action)
            _alert(f"任务 {tid or sid} 冻结期拦截 BUY: {order.symbol}",
                   "不可信 bar / 流序号 gap（数据污染事实）；重启任务解冻。SELL 放行。")
            return None
        _buy_ok = ctx.get("buy_ok")
        if str(order.action).upper() == "BUY" and not (_buy_ok and _buy_ok()):
            # 检查器缺失时保守拒（fail-closed）；非交易时段天然无信号，误拒方向安全
            logger.warning("下单时刻拒 BUY（bar 过期/hub 心跳丢失）: %s", order.symbol)
            _alert(f"任务 {tid or sid} 拦截 BUY（数据不新鲜）: {order.symbol}",
                   "bar 停更>300s 或 hub 心跳丢失；SELL 放行，恢复后自动放行。")
            return None
        return _orig_send(order)

    adapter.send_order = _gated_send

    history = _warmup_history(symbol)

    def _stop_check() -> bool:
        try:
            with _get_conn() as conn:
                if tid is not None:
                    cur = conn.execute("SELECT status FROM live_task WHERE id=%s", (tid,))
                    r_ = cur.fetchone()
                    return bool(r_ and r_[0] == "stopped")
                cur = conn.execute("SELECT enabled FROM strategy_config WHERE id=%s", (sid,))
                r_ = cur.fetchone()
                return bool(r_ and not r_[0])
        except Exception:
            return False

    def _reconcile() -> None:
        """hub worker 启动/重连对账（SC2 同语义简版）。"""
        try:
            from vnpy.trader.constant import Status
            working = [o for o in (adapter.query_orders() or [])
                       if getattr(o, "status", None) in (Status.SUBMITTING, Status.NOTTRADED, Status.PARTTRADED)]
            if working:
                _alert(f"hub worker 对账发现 {len(working)} 笔在场委托（任务 {tid or sid}）", "疑似残留，请人工确认。")
        except Exception as e:
            logger.warning("hub worker 对账失败: %s", e)

    _reconcile()
    ctx.update({
        "tid": tid if tid is not None else sid, "sid": sid, "symbol": symbol,
        "account_id": account_id,
        "strategy": strategy, "adapter": adapter, "event_engine": ee,
        "td_api": td_api, "history": history, "frozen": frozen,
        "initial_capital": initial_capital,
        "warmup_pg": lambda: _warmup_history(symbol),
        "stop_check": _stop_check, "reconcile": _reconcile,
    })
    hub_worker_run(ctx)


def main():
    parser = argparse.ArgumentParser(description="策略实盘化入口")
    parser.add_argument("--task-id", help="live_task.id（新架构：策略与标的分离）")
    parser.add_argument("--id", help="strategy_config.id（旧架构兼容）")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    # 链条打磨#1：runner 启动加载自定义因子（实盘进程此前永不加载）
    try:
        from src.strategy_framework.factor import load_factors_from_db
        _lf = load_factors_from_db()
        if _lf:
            logger.info("加载自定义因子: %s", ", ".join(_lf))
    except Exception as e:
        logger.warning("自定义因子加载失败: %s", e)

    # #48：启动时列级校验（schema 漂移=本地不复现的服务器 500 源，F-16/0038 实锤）
    try:
        from src.data_platform.db import verify_schema
        from src.health_monitor.monitor import report_schema_findings
        report_schema_findings(verify_schema())
    except Exception as e:
        logger.warning("schema 校验异常（不阻断启动）: %s", e)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if MainEngine is None:
        logger.error("vnpy 未安装，策略实盘需要 vnpy 环境")
        sys.exit(EX_CONFIG)

    if not args.task_id and not args.id:
        logger.error("必须提供 --task-id（新）或 --id（旧）")
        sys.exit(EX_CONFIG)

    # SA4：启动依赖探活 + 指数退避（服务器重启序 PG 慢启不再 5 连崩打穿 StartLimit）
    if not _wait_for_deps():
        _alert("实盘任务依赖探活退避耗尽",
               "PG 持续不可达超 10 分钟，runner 以 EX_TEMPFAIL 退出待 systemd/reconciler 重试。")
        sys.exit(EX_TEMPFAIL)

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
            sys.exit(EX_CONFIG)
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
            sys.exit(EX_CONFIG)
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

    # 1.5 md_mode 分派（ST7）：live_task.params.md_mode 覆盖 system_config 全局默认（评审 S5）
    def _md_mode() -> str:
        try:
            with get_conn() as conn:
                cur = conn.execute("SELECT value FROM system_config WHERE key='md_mode'")
                row = cur.fetchone()
                return (params.get("md_mode") or (row[0] if row else None) or "direct").lower()
        except Exception:
            return (params.get("md_mode") or "direct").lower()

    if _md_mode() == "hub":
        _run_hub_mode(sid=sid, tid=tid, name=name, s_type=s_type, symbol=symbol,
                      factors=factors, aggregator=aggregator, params=params,
                      initial_capital=initial_capital, account_id=account_id)
        return

    # 2. 建 MainEngine + XtpGateway（direct 模式）
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)

    try:
        from vnpy_xtp import XtpGateway

        gateway = main_engine.add_gateway(XtpGateway, "XTP")
    except Exception as e:
        logger.error("XtpGateway 加载失败: %s", e)
        main_engine.close()
        sys.exit(EX_CONFIG)
    # 批1（2026-08-25 SEGV 终结防御）：connect 前整体替换为守卫——XtpGateway.__init__
    # 自建 md_api 且 connect() 只调用不重建，此点是官方确认的唯一注入窗
    gateway.md_api = GuardedXtpMdApi(gateway)

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
    # ST7 双轨会话身份（2026-08-25）：XTP 规则=同账号同 client_id 仅一个 MD 会话
    # （官方 CreateQuoteApi 注释），hub（1 号）与 direct runner 必然撞号——direct 轨
    # 用通道级配置 broker_config.params.client_id_runner 的独立号
    _direct_id = get_xtp_param("client_id_runner")
    if _direct_id:
        setting["客户号"] = int(_direct_id)
    if not setting.get("账号") or not setting.get("交易地址"):
        logger.error("XTP 凭证不完整（broker_config 无 xtp 记录，且 .env XTP_TEST_* 未配）")
        sys.exit(EX_CONFIG)
    gateway.connect(setting)
    # L2 会话管理（韧性分层模型 2026-08-24）：direct 模式 MD 登录失败（如 user already
    # exists 会话槽冲突）vnpy_xtp 不重试即永久死（2026-08-24 实锤）--定时续航+反应式重登
    md_sess = XtpMdSession(gateway.md_api)

    # 5. 行情驱动：EVENT_TICK -> BarGenerator -> strategy.on_bar（#4 核心）
    from vnpy.trader.event import EVENT_TICK, EVENT_TRADE
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

    _last_bar = {"ts": None}  # F-8 bar 级幂等：同 ts 重复投递只处理一次

    @_guard("on_bar")
    def on_vnpy_bar(bar):
        d = _bar_to_dict(bar)
        ts_key = str(d.get("ts"))
        if ts_key == _last_bar["ts"]:
            logger.warning("重复 bar 丢弃: %s", ts_key)
            return
        _last_bar["ts"] = ts_key
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
        # ST7 阶段 0 影子落库（bar_shadow，R-BR20 diff 的 direct 侧；1 次/分钟，同步写可接受）。
        # 评审 S5：vnpy bar.datetime=分钟首，hub=分钟末——shadow 统一 +1min 对齐口径，diff 才可比
        try:
            from src.data_platform.db import get_conn as _gc
            from datetime import timedelta as _td
            with _gc() as _conn:
                _conn.execute(
                    "INSERT INTO bar_shadow (symbol, ts, open, high, low, close, volume, amount) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (symbol, ts) DO UPDATE "
                    "SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close, "
                    "volume=EXCLUDED.volume, amount=EXCLUDED.amount",
                    (symbol, d["ts"] + _td(minutes=1), d["open"], d["high"], d["low"], d["close"],
                     d["volume"], getattr(bar, "turnover", 0) or 0))
                _conn.commit()
        except Exception as _se:
            logger.debug("bar_shadow 落库失败（影子期可容忍）: %s", _se)

    bg = BarGenerator(on_vnpy_bar)

    # last_ts/count=进程累计（观测）；sess_*=时段内基线（S6 修订：沿上清零，跨日回放 tick 不污染断流判定）
    _tick_state = {"last_ts": 0.0, "count": 0,
                   "sess_last_ts": 0.0, "sess_count": 0,
                   "sess_enter_ts": 0.0}  # GIL 下原子读写，无需锁

    # S6 修订·direct 下单门（盲审 C-F2 2026-08-18）：与 hub 模式 buy_ok 同语义——BUY 需
    # 交易时段 + tick 新鲜（<300s）。测试平台夜间回放会驱动 on_bar→place_order，无此门则回放
    # 数据真实下单（重复消费旧数据）；时段外拒 BUY 属业务正确，SELL 不受限（R-AV2）。
    _orig_send_direct = adapter.send_order

    def _gated_send_direct(order):
        if str(order.action).upper() == "BUY":
            if not _in_astock_session():
                logger.warning("交易时段外拒 BUY（回放/闭市防护）: %s", order.symbol)
                _alert(f"任务 {tid or sid} 拦截 BUY（交易时段外）: {order.symbol}", "SELL 放行。")
                return None
            _fresh = _tick_state["last_ts"] and (time.time() - _tick_state["last_ts"] < 300)
            if not _fresh:
                logger.warning("下单时刻拒 BUY（tick 过期）: %s", order.symbol)
                _alert(f"任务 {tid or sid} 拦截 BUY（数据不新鲜）: {order.symbol}", "tick 停更>300s；SELL 放行。")
                return None
        return _orig_send_direct(order)

    adapter.send_order = _gated_send_direct

    @_guard("on_tick")
    def on_tick(event):
        _tick_state["last_ts"] = time.time()
        _tick_state["count"] += 1
        if _in_astock_session():
            _tick_state["sess_last_ts"] = _tick_state["last_ts"]
            _tick_state["sess_count"] += 1
        bg.update_tick(event.data)

    event_engine.register(EVENT_TICK, on_tick)

    # SC1（#46/F-7）：成交回报落 trade_log——positions/三账对账从此有真实数据源
    def _write_trade(d) -> None:
        """TradeData → trade_log。幂等：trade_ref 唯一索引 + ON CONFLICT DO NOTHING。"""
        from vnpy.trader.constant import Direction
        try:
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

    @_guard("on_trade")
    def on_trade(event):
        _write_trade(event.data)

    event_engine.register(EVENT_TRADE, on_trade)

    # 订阅 symbol（vt_symbol SHSE -> vnpy Exchange SSE 映射）
    raw, ex = parse_vt_symbol(symbol)
    ex_vnpy = {"SHSE": "SSE", "SZSE": "SZSE", "BSE": "BSE"}.get(ex, ex)
    exchange = getattr(Exchange, ex_vnpy, None)
    if not exchange:
        logger.error("无法解析交易所: %s（vt_symbol=%s）", ex, symbol)
        sys.exit(EX_CONFIG)
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
    sess_was = _in_astock_session()   # 时段沿检测（S6 修订：沿上清 sess_* 基线）

    if sess_was:
        # 盘中启动（人工/自愈重启场景）：时段起点=启动时刻，L2 零 tick 宽限从这起算
        _tick_state["sess_enter_ts"] = time.time()
    _halt_state = {"was": False}  # 熔断沿检测（SB2，F-41：进入熔断的瞬间撤在场单）

    def _startup_reconcile() -> None:
        """SC2：启动对账（v1）。在场委托可见化 + 当日成交补录。

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
                       desc + " —— 疑似上次会话残留，请确认并决定是否人工撤销。")
            trades = adapter.query_trades() or []
            n_new = 0
            for t in trades:
                before = _tick_state["count"]  # noqa: F841（占位，实际以 _write_trade 内部判断为准）
                _write_trade(t)
                n_new += 1
            if trades:
                logger.info("启动对账：补录当日成交 %d 笔（trade_ref 幂等去重）", n_new)
            # 提交中残留（WAL 崩溃窗口证据）：上一会话 submitting 但无对应成交/委托 → 标记
            try:
                with get_conn() as conn:
                    cur = conn.execute(
                        "SELECT id, symbol, action, volume FROM order_log WHERE strategy_id=%s "
                        "AND status='submitting' AND ts::date=current_date", (sid,))
                    orphans = cur.fetchall()
                for oid, osym, oact, ovol in orphans:
                    logger.warning("WAL 残留 submitting 单 id=%s %s %s %s（上会话崩溃窗口），待人工核对", oid, osym, oact, ovol)
                if orphans:
                    _alert(f"WAL 残留 {len(orphans)} 笔 submitting 委托（任务 {sid}）",
                           "上一会话在'记账后、确认前'中断。请对照券商委托列表核对后人工处理。")
            except Exception as e:
                logger.warning("WAL 残留检查失败: %s", e)
        except Exception as e:
            logger.warning("启动对账失败: %s", e)

    try:
        while True:
            time.sleep(10)
            counter += 1
            if counter == 1:
                # SC2：首轮（登录已完成）做启动对账
                _startup_reconcile()
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
            # 链条打磨#6：同标记兼作因子热重载钩子（Web 改因子后 runner 不重启即生效）
            try:
                if _r.get("factor:recalc:triggered"):
                    try:
                        from src.strategy_framework.factor import load_factors_from_db
                        load_factors_from_db()
                    except Exception:
                        pass
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
            # 3) tick 新鲜度（F-18/F-25，S6 修订 2026-08-18）：断流只告警不退出——
            #    重启治不了平台/网络问题，进程级故障由 watchdog/事件线程检查兜。
            #    基线=本时段内首 tick（进入沿清零）：跨日回放/假日/竞价静默窗口不误判。
            sess_now = _in_astock_session()
            if session_edge(sess_now, sess_was):
                _tick_state["sess_last_ts"] = 0.0
                _tick_state["sess_count"] = 0
                if sess_now:
                    # 进沿写时段起点（hub 同款）：反应式重登的零 tick 宽限从此起算。
                    # 缺此行 = 盘外启动的进程整时段 _symptom 恒假，反应式重登死路
                    # （2026-08-25 实锤：昨 18:32 部署重启的 runner 今早 09:31-10:13 零重试）。
                    _tick_state["sess_enter_ts"] = time.time()
            sess_was = sess_now
            # --- L2 会话自愈（韧性分层模型 2026-08-24）---
            # 定时续航：交易日 09:10 开盘前换新鲜会话（XTP 日切 ≈23:53 丢会话，2026-08-24 实锤）
            try:
                if md_sess.schedule_due():
                    logger.info("定时续航：交易日开盘前重登 MD 会话（任务 %s）", sid or tid)
                    md_sess.renew()
            except Exception as e:
                logger.warning("MD 定时续航检查失败: %s", e)
            # 反应式重登：盘中症状驱动（零 tick 超宽限=僵尸会话 / 断流超 5min）
            try:
                _sess_enter = _tick_state.get("sess_enter_ts", 0.0)
                _symptom = (sess_now and _tick_state["sess_count"] == 0
                            and _sess_enter and time.time() - _sess_enter > 600)
                if not _symptom and sess_now and _tick_state["sess_count"] > 0 and _tick_state["sess_last_ts"]:
                    _symptom = time.time() - _tick_state["sess_last_ts"] > 300
                if _symptom and md_sess.retry_ready():
                    logger.warning("MD 症状驱动重登（僵尸会话/断流，任务 %s）", sid or tid)
                    _alert("实盘任务 MD 反应式重登",
                           f"盘中零 tick 超 10 分钟或断流超 5 分钟（任务 {sid or tid}），"
                           f"进程内重登行情会话（不重启进程）。持续未恢复请查 XTP 平台状态。")
                    md_sess.renew()
                if _tick_state["sess_last_ts"] and time.time() - _tick_state["sess_last_ts"] < 60:
                    md_sess.on_recovered()
            except Exception as e:
                logger.warning("MD 反应式重登检查失败: %s", e)
            _sess_stale = (time.time() - _tick_state["sess_last_ts"]) if _tick_state["sess_last_ts"] else None
            if sess_now:
                if _tick_state["sess_count"] == 0 and counter % 30 == 0:
                    _alert(f"实盘任务交易时段零 tick: {sid}",
                           "订阅可能未生效/XTP 异常。进程内自动重登中（定时续航/反应式，不重启进程）；"
                           "持续未恢复请查 XTP 平台状态与 journalctl 中 [gw] 日志。")
                if _tick_state["sess_count"] > 0 and _sess_stale is not None and _sess_stale > 120 and counter % 6 == 0:
                    _lvl = logger.critical if _sess_stale > 300 else logger.error
                    _lvl("tick 断流 %.0fs（时段内已收 %d 条，只告警不退出）", _sess_stale, _tick_state["sess_count"])
                    _alert(f"实盘任务 tick 断流: {sid}",
                           f"已断流 {_sess_stale:.0f}s（时段内已收 {_tick_state['sess_count']} 条）。"
                           f"进程内自动重登中；持续未恢复请查 XTP 平台状态与 journalctl 中 [gw] 日志。")
                # 4) 幂等重订阅（F-24/F-25）：交易时段每 60s 重放一次，兜住"重连后订阅丢失"
                if counter % 6 == 0:
                    _resubscribe()
            # 5) Valkey 心跳（供巡检/看板；失联只记日志）
            try:
                _r.hset(f"quant:hb:task:{tid or sid}", mapping={
                    "pid": os.getpid(), "ts": time.time(),
                    "last_tick_ts": _tick_state["last_ts"] or 0,
                    "ticks": _tick_state["count"], "sess_ticks": _tick_state["sess_count"],
                    "bars": len(history),
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
                                         (total, daily_pnl, _account_baseline_capital(total), avail))
                            # ST2：同拍写持仓真相批（N-v2：取返回值单事务覆盖，非 EVENT_POSITION handler）
                            _flush_positions(adapter, account_id, tid)
                            conn.commit()
                except Exception as e:
                    logger.warning("写 account_snapshot 失败: %s", e)
    except KeyboardInterrupt:
        logger.info("策略 %s 停止", sid)
    finally:
        # P1 修复（2026-08-20 双盲审计 A3）：live_task 状态回写——原只在 Web 路径写，
        # systemd stop/StartLimit Failed/机器重启后 status 永久残留 running
        # （Web 假运行中 + hub _desired_symbols 永续订阅）。
        if tid is not None:
            try:
                with get_conn() as conn:
                    cur = conn.execute('SELECT status FROM live_task WHERE id=%s', (tid,))
                    r = cur.fetchone()
                    if r and r[0] == 'running':
                        conn.execute("UPDATE live_task SET status='stopped' WHERE id=%s", (tid,))
                        conn.commit()
                        logger.info("退出回写 live_task %s → stopped", tid)
            except Exception as e:
                logger.warning("退出状态回写失败: %s", e)
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