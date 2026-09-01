"""策略实盘化入口（#4 修正版 B；批 6b 起 hub 模式唯一）。

每任务独立子进程（hub 模式，ST7 14 号设计 v2）：
  live_task -> ThinTdGateway(TD-only) + XTPAdapter + hub_worker（消费 hub:bars:* 流）。
  direct 模式（进程内 MainEngine+XtpGateway 自采行情）2026-09-01 批 6b 退役——
  md_mode=direct 显式 EX_CONFIG 拒绝（历史 git 史可考）。

启动: python -m src.strategy_runner.main --task-id <live_task.id>
systemd: systemctl start quant-live-task@<task_id>
"""
import argparse
import logging
import sys
import time

logger = logging.getLogger("strategy_runner")

try:
    from vnpy.event import EventEngine
except ImportError:
    EventEngine = None


# 2026-08-19 模块归位：build_xtp_setting 搬 strategy_framework/broker（hub/runner 双消费方）；
# 此别名保 tests/scripts 旧 import 兼容
from src.strategy_framework.broker import build_xtp_setting as _build_xtp_setting

# 批 4a（2026-08-27）：交易域九单元单源化于 trading（write_trade_log/快照/熔断沿/recalc/
# stop_due/对账/frozen/buy_ok/_flush_positions）——direct 与 hub worker 共享，语义与提取前
# 零漂移（知情差异五条见 docs/任务/批4-worker迁移与trading解耦.md v2.1）
from src.strategy_runner import trading

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
# 批 4a：_flush_positions / _account_baseline_capital 提取至 trading.py（direct 与 hub worker 单源）

# 2026-08-19 模块归位：guard/sd_notify/session 来自 quant_common（本包禁止依赖告警层，
# alert 回调在此注入——safe_notify 收编三处重复 try/except notify 模式）
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
    from vnpy.event import EventEngine
    from vnpy.trader.gateway import BaseGateway
    from vnpy_xtp.gateway.xtp_gateway import XtpTdApi
    from src.strategy_framework.strategy import Strategy, StrategyConfig
    from src.strategy_framework.adapters import XTPAdapter
    from src.strategy_runner.hub_worker import run as hub_worker_run

    logger.info("任务 %s 以 hub 模式启动（策略 %s 标的 %s）", tid or sid, sid, symbol)
    setting = _build_xtp_setting()
    boot_epoch = int(time.time())   # 评审 S8：秒级 epoch（分钟级同分钟重启会撞 id）

    # 每日连接窗·TD 侧（P2 批 08-28，A/B 双盲审）：窗开建连/窗关启动不连（窗开沿由
    # hub_worker._td_reconnect 补首连）；盘后不断开（XtpTdApi 无 logout，exit 循环无
    # 实证不冒——工程分级，TD 全窗化二期）。lead/lag 任一 0=禁用日窗（永久连接）。
    from datetime import datetime as _dtnow   # 盲审 A-P0：函数级导入（模块头部无 datetime）
    from src.strategy_framework.md_session import is_trading_day as _itd
    from src.strategy_framework.md_session import load_xtp_window_cfg, xtp_session_window_open
    _lead, _lag = load_xtp_window_cfg()
    _td_open = xtp_session_window_open(_dtnow.now(), _lead, _lag, trading_day=_itd())

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
    ee.start()   # 同 md_hub：直连 EventEngine（不经 MainEngine）须自启

    # 批 6b（EVENT_LOG 修，批 4 迁移遗漏）：TD 会话日志（连接/断开/重登/拒单）走
    # EVENT_LOG——hub 模式此前未注册全被吞（md_hub 批 0 修过同款盲区）。vnpy_xtp
    # TD 侧仅事件驱动低频（盲审 B 实核 xtp_gateway.py:493/585/744/766），不刷屏。
    from vnpy.trader.event import EVENT_LOG

    @_guard("worker.on_log")
    def on_log(event):
        logger.info("[gw] %s", getattr(event.data, "msg", event.data))
    ee.register(EVENT_LOG, on_log)

    gw = ThinTdGateway(ee, "XTP")
    td_api = XtpTdApi(gw)
    gw.td_api = td_api
    if _td_open:
        gw.connect(setting)   # 只连 TD（R-TD1：hub 零 TD，worker 零 MD）
    else:
        logger.info("TD 窗关启动（lead=%d/lag=%d），连接待窗开沿", _lead, _lag)

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

    def _gated_send(order):
        if not trading.frozen_allows(order.action, frozen):
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
        # 4a 单源化：停止判定收口 trading.stop_due（worker 5s 节奏在 hub_worker.run 调用侧保持）
        return trading.stop_due(tid, sid)

    def _reconcile() -> None:
        """hub worker 启动/重连对账（4a 单源化：runner 超集=在场委托+成交补录+WAL 残留，
        知情差异①——worker 由只告警在场委托升级，启动与 TD 重连沿均变化，知情接受）。"""
        trading.reconcile_orders(adapter, sid, symbol)

    if _td_open:
        _reconcile()   # B-P1-1：TD 在线才有对账意义——窗关启动跳过，窗开沿 connect 后由 TD 重连沿触发
    ctx.update({
        "tid": tid if tid is not None else sid, "sid": sid, "symbol": symbol,
        "account_id": account_id,
        "strategy": strategy, "adapter": adapter, "event_engine": ee,
        "td_api": td_api, "history": history, "frozen": frozen,
        "initial_capital": initial_capital,
        "warmup_pg": lambda: _warmup_history(symbol),
        "stop_check": _stop_check, "reconcile": _reconcile,
        "td_connect": lambda: gw.connect(setting),      # 窗开沿建连（P2 批 08-28）
        "td_window": (_lead, _lag),                     # 同窗参数（worker 轮询）
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

    if EventEngine is None:
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

    # 1.5 md_mode 校验（批 6b：direct 退役）：hub 是唯一实盘行情模式。误设 direct
    # 显式 EX_CONFIG fail-fast（盲审 B-P1：静默落入已删代码=任务装死无人知）。
    _md = str(params.get("md_mode") or "").lower()
    if not _md:
        try:
            with get_conn() as conn:
                row = conn.execute("SELECT value FROM system_config WHERE key='md_mode'").fetchone()
                _md = str(row[0] if row else "").lower()
        except Exception:
            pass
    if _md == "direct":
        logger.error("md_mode=direct 已退役（批 6b，2026-09-01）：实盘行情统一 hub 模式。"
                     "请清除 live_task.params.md_mode 或 system_config.md_mode 的 direct 值")
        sys.exit(EX_CONFIG)

    _run_hub_mode(sid=sid, tid=tid, name=name, s_type=s_type, symbol=symbol,
                  factors=factors, aggregator=aggregator, params=params,
                  initial_capital=initial_capital, account_id=account_id)
    return


if __name__ == "__main__":
    main()
