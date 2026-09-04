"""回测引擎 -- 基于策略框架的标准回测，产出净值/胜率/回撤/夏普。

不依赖 VeighNa CtaTemplate，直接用我们的 Strategy 基类 + 历史 K 线。
"""

from __future__ import annotations
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable
import numpy as np
import pandas as pd

import logging

from .strategy import Strategy, StrategyConfig, Signal, Action, SignalAggregator
from .adapters import ExecutionAdapter, Order, Position

logger = logging.getLogger("backtest")
_patch_lock = threading.Lock()


@dataclass
class Trade:
    """一笔成交。"""
    ts: datetime
    symbol: str
    action: str       # BUY / SELL
    volume: int
    price: float
    commission: float = 0.0


@dataclass
class BacktestResult:
    """回测结果。"""
    # 概要
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 0.0
    final_value: float = 0.0
    total_return_pct: float = 0.0
    # 绩效指标
    win_rate: float = 0.0          # 胜率
    max_drawdown_pct: float = 0.0  # 最大回撤
    sharpe_ratio: float = 0.0     # 夏普比率（年化）
    volatility: float = 0.0       # 年化波动率（B5 #21）
    sortino_ratio: float = 0.0    # 索提诺比率（下行，B5 #21）
    alpha: float = 0.0           # α（P3-2，需基准数据，默认 0）
    beta: float = 0.0            # β（P3-2，需基准数据，默认 0）
    information_ratio: float = 0.0  # 信息率（P3-2）
    benchmark_return: float = 0.0   # 基准收益（P3-2）
    benchmark_volatility: float = 0.0  # 基准波动率（ptrade 批 1，年化 %）
    total_trades: int = 0          # 总交易次数
    # 明细
    daily_values: list = field(default_factory=list)   # 每日净值
    trades: list = field(default_factory=list)          # 成交记录
    metrics: dict = field(default_factory=dict)         # 全部指标
    logs: list = field(default_factory=list)            # 回测日志（ptrade 批 1：run 作用域捕获）


class BacktestAdapter(ExecutionAdapter):
    """回测适配器：订单按当前 bar 收盘价成交，记录交易。"""

    def __init__(self):
        self.trades: list[Trade] = []
        self.rejected_limit: list[dict] = []   # P2-5：涨跌停拒成交记录（05 §5.7 A股约束）
        self._current_bar: dict = {}
        self._commission_rate: float = 0.0005  # 万五
        self._slippage: float = 0.0
        # P2-5（web-design 05 §5.7）：A股费用摩擦参数化——印花税（仅卖出，2023-08 减半后 0.05%）
        # +过户费（0.001%，双边）；不做这些=系统性偏乐观（盲审一补充）
        self._stamp_tax = 0.0005
        self._transfer_fee = 0.00001
        self._limit_lock = True   # 涨跌停不可成交约束（一字板近似：high==low）

    def set_bar(self, bar: dict):
        self._current_bar = bar

    def set_commission(self, rate: float):
        self._commission_rate = rate

    def set_slippage(self, slip: float):
        self._slippage = slip

    def set_fees(self, stamp_tax: float | None = None, transfer_fee: float | None = None,
                 limit_lock: bool | None = None):
        """P2-5 费用摩擦参数化入口（Web 发起回测可配）。"""
        if stamp_tax is not None:
            self._stamp_tax = max(0.0, stamp_tax)
        if transfer_fee is not None:
            self._transfer_fee = max(0.0, transfer_fee)
        if limit_lock is not None:
            self._limit_lock = limit_lock

    def send_order(self, order: Order) -> str | None:
        # P0-6 修复（2026-08-20 双盲审计 B1）：slippage 曾是死参数——BUY 按 close*(1+slip)
        # 吃进、SELL 按 close*(1-slip) 出货，负滑点让回测系统性偏乐观
        base = self._current_bar.get("close", 0)
        high = self._current_bar.get("high", base)
        low = self._current_bar.get("low", base)
        # P2-5 涨跌停不可成交（一字板近似）：BUY 遇一字涨停（high==low 且收在涨停）不成交；
        # SELL 遇一字跌停不成交——真实世界挂单也排不进。近似口径：当日 high==low（无波动）
        if self._limit_lock and high == low and high > 0:
            self.rejected_limit.append({"ts": self._current_bar.get("ts"), "symbol": order.symbol,
                                        "action": order.action, "reason": "limit_lock"})
            return None
        slip = self._slippage if self._slippage >= 0 else 0.0
        price = base * (1 + slip) if order.action == "BUY" else base * (1 - slip)
        ts = self._current_bar.get("ts", datetime.now())
        amount = price * order.volume
        commission = amount * self._commission_rate
        # 过户费双边；印花税仅卖出
        commission += amount * self._transfer_fee
        if order.action == "SELL":
            commission += amount * self._stamp_tax
        self.trades.append(Trade(
            ts=ts, symbol=order.symbol, action=order.action,
            volume=order.volume, price=price, commission=commission,
        ))
        return f"bt-{len(self.trades)}"

    def cancel_order(self, order_id: str) -> None:
        pass

    def query_position(self) -> list[Position]:
        return []


class BacktestEngine:
    """回测引擎。

    用法:
        engine = BacktestEngine(initial_capital=1_000_000)
        result = engine.run(strategy_config, bars_df)
        print(result.total_return_pct, result.max_drawdown_pct, result.sharpe_ratio)
    """

    def __init__(self, initial_capital: float = 1_000_000,
                 commission_rate: float = 0.0005,
                 slippage: float = 0.0):
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage = slippage

    def run(self, config: StrategyConfig, bars: list[dict],
            shares_per_trade: int = 100,
            on_bar_callback: Callable | None = None,
            benchmark_bars: list | None = None) -> BacktestResult:
        """运行回测。

        Args:
            config: 策略配置
            bars: K 线列表 [{ts, open, high, low, close, volume, ...}, ...]
            shares_per_trade: 每笔交易股数
        """
        adapter = BacktestAdapter()
        adapter.set_commission(self.commission_rate)
        adapter.set_slippage(self.slippage)   # P0-6：slippage 接线（曾死参）
        strategy = Strategy.from_config(config, adapter)

        # 回测数据完整性预检
        pc = precheck_backtest_data(config, bars)
        if not pc["valid"]:
            return BacktestResult(metrics={"error": "回测数据预检失败", "issues": pc["issues"]})

        # 防未来函数校验
        v = validate_no_future_data(strategy)
        if not v["valid"]:
            return BacktestResult(metrics={"error": "防未来函数校验失败", "details": v["warnings"]})

        # 回测模式：跳过风控（monkey-patch，线程安全）
        import src.strategy_framework.strategy as strat_mod
        original_place_order = strat_mod.Strategy.place_order
        def _bt_place_order(self, sig: Signal):
            order = Order(
                symbol=self.symbol,
                action=sig.action.name,
                volume=shares_per_trade,
                price=sig.price or adapter._current_bar.get("close", 0),
            )
            adapter.send_order(order)
        with _patch_lock:
            strat_mod.Strategy.place_order = _bt_place_order

        # 回测日志（ptrade 批 1）：显式收集器——run_logs 是 run 局部变量，strategy._log_fn 注入收集，
        # 无全局 handler/contextvar/setLevel 共享状态，进程内并发天然安全
        run_logs: list = []

        def _log_fn(msg, level="info"):
            run_logs.append({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             "level": str(level).upper(), "msg": str(msg)})

        strategy._log_fn = _log_fn

        try:
            cash = self.initial_capital
            position = 0
            avg_price = 0.0
            daily_values = []
            history: list[dict] = []  # 累积历史 K 线
            wins = 0
            total_closed = 0
            buy_queue = deque()

            for i, bar in enumerate(bars):
                adapter.set_bar(bar)

                # 策略计算（传入历史）
                sig = strategy.on_bar(bar, history=history)
                # 当前 bar 入历史
                history.append(bar)

                # 处理成交
                # P0-6 修复（双盲审计 B2 幻影成交）：资金不足/持仓不足的 Trade 从明细移除——
                # 原实现静默跳过配账但 trades/total_trades/胜率照录未成交单
                for trade in list(adapter.trades):
                    if trade.ts == bar.get("ts") and trade.symbol == config.symbol:
                        if trade.action == "BUY":
                            cost = trade.price * trade.volume + trade.commission
                            if cash >= cost:
                                cash -= cost
                                total = position * avg_price + trade.volume * trade.price
                                position += trade.volume
                                avg_price = total / position if position > 0 else 0
                                buy_queue.append((trade.volume, trade.price))
                            else:
                                adapter.trades.remove(trade)   # 未成交不入明细
                                _log_fn(f"资金不足，BUY 未成交: {trade.symbol} {trade.volume}股 @{trade.price}（现金 {cash:.2f}）", "warning")
                        elif trade.action == "SELL":
                            if position >= trade.volume:
                                proceeds = trade.price * trade.volume - trade.commission
                                cash += proceeds
                                # FIFO 逐笔配对胜率判定
                                remaining = trade.volume
                                while remaining > 0 and buy_queue:
                                    buy_vol, buy_price = buy_queue.popleft()
                                    match_vol = min(remaining, buy_vol)
                                    if trade.price > buy_price:
                                        wins += 1
                                    total_closed += 1
                                    remaining -= match_vol
                                    if buy_vol > match_vol:
                                        buy_queue.appendleft((buy_vol - match_vol, buy_price))
                                position -= trade.volume
                                if position == 0:
                                    avg_price = 0.0
                            else:
                                adapter.trades.remove(trade)   # 未成交不入明细
                                _log_fn(f"持仓不足，SELL 未成交: {trade.symbol} {trade.volume}股 @{trade.price}（持仓 {position}）", "warning")

                # 每日净值
                close = bar.get("close", 0)
                portfolio_value = cash + position * close
                daily_values.append({
                    "ts": str(bar.get("ts")),   # 转 str：pandas Timestamp 不可 JSON 序列化（回测 result 存库崩溃→symbol error）
                    "cash": round(cash, 2),
                    "position": position,
                    "avg_price": round(avg_price, 4),   # 加权买入均价（非 FIFO 成本：SELL 不减价，展示持仓盈亏用）
                    "close": close,
                    "value": round(portfolio_value, 2),
                })

                # B1: on_bar 回调（B3 推 progress/equity/trades 到 Valkey，§三① 契约）
                if on_bar_callback:
                    on_bar_callback(bar, {
                        "position": position,
                        "avg_price": avg_price,
                        "equity": portfolio_value,
                        "cash": cash,
                        "trades": [{"ts": str(t.ts)[:19], "symbol": t.symbol, "action": t.action,
                                    "volume": t.volume, "price": t.price} for t in adapter.trades],
                        "log": "",
                        "progress": {"current": i + 1, "total": len(bars),
                                     "pct": round((i + 1) / len(bars) * 100, 1)},
                    })

            # 清仓
            if position > 0 and bars:
                last_close = bars[-1].get("close", 0)
                cash += position * last_close
                position = 0

            final_value = cash
        finally:
            # 恢复（线程安全）
            with _patch_lock:
                strat_mod.Strategy.place_order = original_place_order

        # 计算指标
        return self._calculate(
            daily_values, adapter.trades, final_value,
            bars[0].get("ts") if bars else "",
            bars[-1].get("ts") if bars else "",
            wins, total_closed, benchmark_bars, run_logs,
        )

    def _calculate(self, daily_values: list, trades: list[Trade],
                   final_value: float, start_ts, end_ts,
                   wins: int = 0, total_closed: int = 0,
                   benchmark_bars: list | None = None,
                   run_logs: list | None = None) -> BacktestResult:
        """计算绩效指标。benchmark_bars 为基准指数 bars（[{ts, close}, ...]），可选。"""
        if not daily_values:
            return BacktestResult(logs=run_logs or [])

        values = [d["value"] for d in daily_values]
        initial = self.initial_capital
        final = final_value
        total_return = (final / initial - 1) * 100 if initial > 0 else 0

        # 每日收益率
        returns = []
        for i in range(1, len(values)):
            if values[i - 1] > 0:
                returns.append(values[i] / values[i - 1] - 1)

        # 最大回撤
        peak = values[0]
        max_dd = 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # 夏普比率（年化，假设 252 交易日，无风险利率 2%）
        if returns:
            avg_return = np.mean(returns)
            std_return = np.std(returns)
            sharpe = (avg_return - 0.02 / 252) / std_return * np.sqrt(252) if std_return > 0 else 0
            # 波动率（年化）+ 索提诺（下行 std）（B5 #21）
            volatility = float(std_return * np.sqrt(252) * 100)
            downside = [r for r in returns if r < 0]
            downside_std = np.std(downside) if downside else 0
            sortino = (avg_return - 0.02 / 252) / downside_std * np.sqrt(252) if downside_std > 0 else 0
        else:
            sharpe = 0
            volatility = 0.0
            sortino = 0.0

        # 基准对比（ptrade 批 1）：α/β/信息率/基准收益/基准波动率（复用 _benchmark_metrics）
        bm = _benchmark_metrics(daily_values, benchmark_bars)
        alpha = bm["alpha"]
        beta = bm["beta"]
        information_ratio = bm["information_ratio"]
        benchmark_return = bm["benchmark_return"]
        benchmark_volatility = bm["benchmark_volatility"]

        # 滚动绩效（ptrade 批 1）：月度 1/3/6/12 窗口指标；空基准时基准字段自然为 0（非基准指标仍算）
        rolling = rolling_metrics(daily_values, benchmark_bars)

        # 胜率（从主循环传入）
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0

        return BacktestResult(
            start_date=str(start_ts)[:10] if start_ts else "",
            end_date=str(end_ts)[:10] if end_ts else "",
            initial_capital=initial,
            final_value=round(final, 2),
            total_return_pct=round(total_return, 2),
            win_rate=round(win_rate, 1),
            max_drawdown_pct=round(max_dd, 2),
            sharpe_ratio=round(sharpe, 2),
            volatility=round(volatility, 2),
            sortino_ratio=round(sortino, 2),
            alpha=round(alpha, 4),
            beta=round(beta, 4),
            information_ratio=round(information_ratio, 4),
            benchmark_return=benchmark_return,
            benchmark_volatility=round(benchmark_volatility, 2),
            total_trades=len(trades),
            daily_values=daily_values,
            trades=[{
                "ts": str(t.ts)[:19], "symbol": t.symbol, "action": t.action,
                "volume": t.volume, "price": t.price, "commission": round(t.commission, 2),
            } for t in trades],
            metrics={
                "initial_capital": initial,
                "final_value": round(final, 2),
                "total_return_pct": round(total_return, 2),
                "max_drawdown_pct": round(max_dd, 2),
                "sharpe_ratio": round(sharpe, 2),
                "volatility": round(volatility, 2),
                "sortino_ratio": round(sortino, 2),
                "alpha": round(alpha, 4),
                "beta": round(beta, 4),
                "information_ratio": round(information_ratio, 4),
                "benchmark_return": benchmark_return,
                "benchmark_volatility": round(benchmark_volatility, 2),
                "win_rate": round(win_rate, 1),
                "total_trades": len(trades),
                "commission_rate": self.commission_rate,
                "rolling": rolling,
            },
            logs=run_logs or [],
        )


def _align_benchmark_returns(daily_values: list, benchmark_bars: list) -> tuple[list, list]:
    """对齐策略与基准的逐日收益率（按日期 inner join，方案定稿四·8 对齐）。

    策略 daily_values 的 value → 逐日收益率；基准 benchmark_bars 的 close → 逐日收益率。
    公共交易日 inner join，停牌/缺日跳过。返回 (r_p, r_b)。
    """
    strat_val = {str(d["ts"])[:10]: float(d["value"]) for d in daily_values}
    bench_close = {str(b["ts"])[:10]: float(b["close"]) for b in benchmark_bars}
    common = sorted(set(strat_val) & set(bench_close))
    r_p, r_b = [], []
    for i in range(1, len(common)):
        d, prev = common[i], common[i - 1]
        if strat_val[prev] > 0 and bench_close[prev] > 0:
            r_p.append(strat_val[d] / strat_val[prev] - 1)
            r_b.append(bench_close[d] / bench_close[prev] - 1)
    return r_p, r_b


def _benchmark_metrics(daily_values: list, benchmark_bars: list) -> dict:
    """算基准对比指标（ptrade 批 1，口径方案定稿四）：α/β/信息率/基准收益/基准波动率/策略波动率。

    从 daily_values 与 benchmark_bars 按日期对齐的收益率算。无基准时全 0。
    """
    out = {"alpha": 0.0, "beta": 0.0, "information_ratio": 0.0,
           "benchmark_volatility": 0.0, "benchmark_return": 0.0, "volatility": 0.0}
    if not benchmark_bars:
        return out
    r_p, r_b = _align_benchmark_returns(daily_values, benchmark_bars)
    if len(r_p) > 1 and len(r_b) > 1:
        rf = 0.02 / 252
        var_b = float(np.var(r_b, ddof=0))
        if var_b > 0:
            out["beta"] = float(np.cov(r_p, r_b, ddof=0)[0, 1] / var_b)
            out["alpha"] = float((np.mean(r_p) - rf - out["beta"] * (np.mean(r_b) - rf)) * 252)
        # else：基准恒定，α/β 保持 0（Jensen α 无定义）
        active = [a - b for a, b in zip(r_p, r_b)]
        active_std = float(np.std(active, ddof=0))
        out["information_ratio"] = float(np.mean(active) / active_std * np.sqrt(252)) if active_std > 0 else 0.0
        out["benchmark_volatility"] = float(np.std(r_b, ddof=0) * np.sqrt(252) * 100)
        out["volatility"] = float(np.std(r_p, ddof=0) * np.sqrt(252) * 100)
    # benchmark_return：同期累计收益，截断到 daily_values 的日期范围（窗口截断，盲审 P1）
    dv_dates = {str(d["ts"])[:10] for d in daily_values}
    b_closes = [float(b["close"]) for b in benchmark_bars
                if str(b["ts"])[:10] in dv_dates and b.get("close")]
    if len(b_closes) > 1 and b_closes[0] > 0:
        out["benchmark_return"] = round((b_closes[-1] / b_closes[0] - 1) * 100, 2)
    return out


def _window_metrics(win_dv: list, benchmark_bars: list) -> dict:
    """窗口内完整指标（ptrade 批 1 滚动绩效）：收益/回撤/夏普/索提诺 + 基准 α/β 等。"""
    values = [d["value"] for d in win_dv]
    total_return = (values[-1] / values[0] - 1) * 100 if values[0] > 0 else 0
    returns = [values[j] / values[j - 1] - 1 for j in range(1, len(values)) if values[j - 1] > 0]
    if returns:
        avg = float(np.mean(returns)); std = float(np.std(returns))
        sharpe = (avg - 0.02 / 252) / std * np.sqrt(252) if std > 0 else 0
        downside = [r for r in returns if r < 0]
        dstd = float(np.std(downside)) if downside else 0
        sortino = (avg - 0.02 / 252) / dstd * np.sqrt(252) if dstd > 0 else 0
    else:
        sharpe = sortino = 0
    peak = values[0]; max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
    bm = _benchmark_metrics(win_dv, benchmark_bars)
    # volatility 与 sharpe/sortino 同源（窗口连续日收益率 returns），非对齐 r_p（盲审 P2）
    volatility = float(np.std(returns) * np.sqrt(252) * 100) if returns else 0.0
    return {"return": round(total_return, 2), "max_drawdown": round(max_dd, 2),
            "sharpe": round(sharpe, 2), "sortino": round(sortino, 2),
            "alpha": round(bm["alpha"], 4), "beta": round(bm["beta"], 4),
            "information_ratio": round(bm["information_ratio"], 4),
            "volatility": round(volatility, 2),
            "benchmark_return": bm["benchmark_return"],
            "benchmark_volatility": round(bm["benchmark_volatility"], 2)}


def rolling_metrics(daily_values: list, benchmark_bars: list) -> dict:
    """月度滚动绩效（ptrade 批 1）：每个结束自然月，算 1/3/6/12 月窗口的指标。

    返回 {month: {window: metric_dict}}，month="YYYY-MM"，window ∈ {"1","3","6","12"}。
    窗口按日历月算术（严格 N 自然月，非有数据月计数），数据不足（< 2 根）时该窗口为 None。
    """
    def _month(ts):
        return str(ts)[:7]

    def _month_add(m, delta):
        y, mo = int(m[:4]), int(m[5:7])
        idx = (y * 12 + (mo - 1)) + delta
        return f"{idx // 12:04d}-{idx % 12 + 1:02d}"

    months = sorted({_month(d["ts"]) for d in daily_values})
    result: dict = {}
    for i, m in enumerate(months):
        result[m] = {}
        for win in (1, 3, 6, 12):
            if i + 1 < win:
                result[m][str(win)] = None   # 时间不足 win 个月（回测第 i+1 个月）
                continue
            start_month = _month_add(m, -(win - 1))
            win_dv = [d for d in daily_values if start_month <= _month(d["ts"]) <= m]
            result[m][str(win)] = _window_metrics(win_dv, benchmark_bars) if len(win_dv) >= 2 else None
    return result


# --- 防未来函数校验 ---

def validate_no_future_data(strategy: Strategy) -> dict:
    """校验策略因子不会引用未来数据。

    架构层面已防护：BarContext 只有 close/high/low/open_/volume（当前 bar）
    和 _history（过去 bar 列表），无任何 future 属性。此函数做显式断言。

    Returns: {"valid": bool, "checks": [...], "warnings": [...]}
    """
    checks = []
    warnings = []

    # 1. BarContext 无 future 属性
    from .factor import BarContext
    ctx_attrs = [a for a in dir(BarContext) if not a.startswith('_')]
    forbidden = ['future', 'next', 'tomorrow', 'bars_after', 'forward']
    found_forbidden = [a for a in ctx_attrs if any(f in a.lower() for f in forbidden)]
    if found_forbidden:
        checks.append(f"✗ BarContext 有禁用属性: {found_forbidden}")
    else:
        checks.append("✓ BarContext 无 future/next/forward 属性")

    # 2. 回测引擎 history 追加在 on_bar 之后
    checks.append("✓ BacktestEngine: history.append(bar) 在 on_bar 之后，因子只见过去")

    # 3. DSL 因子表达式只用当前 bar 值
    for f in strategy._factors:
        if hasattr(f, 'expr'):
            # DSL 因子检查表达式不含 future 关键词
            expr_lower = f.expr.lower()
            if any(kw in expr_lower for kw in ['future', 'next', 'shift(-', 'shift( -']):
                warnings.append(f"✗ DSL 因子 {f.name} 表达式含未来引用: {f.expr}")
            else:
                checks.append(f"✓ DSL 因子 {f.name} 表达式无未来引用")

    # 4. 因子 compute 签名检查（只接收 BarContext，不接收全量 bars）
    import inspect
    for f in strategy._factors:
        sig = inspect.signature(f.compute)
        params = list(sig.parameters.keys())
        if len(params) > 1 or (params and params[0] != 'ctx' and params[0] != 'self'):
            warnings.append(f"✗ 因子 {f.name} compute 参数异常: {params}")
        else:
            checks.append(f"✓ 因子 {f.name} compute 只接收 BarContext")

    return {
        "valid": len([w for w in warnings if w.startswith('✗')]) == 0,
        "checks": checks,
        "warnings": warnings,
    }


# --- 回测数据完整性预检 ---

def precheck_backtest_data(config: StrategyConfig, bars: list[dict]) -> dict:
    """F-MOD-004 回测前数据完整性校验。

    检查：数据量充足、时序连续、无断点、因子窗口覆盖。
    """
    issues = []
    checks = []

    if not bars:
        return {"valid": False, "issues": ["无 K 线数据"], "checks": ["✗ bars 为空"]}

    # 1. 数据量 >= 最大因子窗口
    max_window = 1
    for f in config.factors:
        params = f.get("params", {})
        n = params.get("n", 20)
        if isinstance(n, int):
            max_window = max(max_window, n)
    if len(bars) < max_window:
        issues.append(f"数据量 {len(bars)} < 最大因子窗口 {max_window}")
    else:
        checks.append(f"✓ 数据量 {len(bars)} >= 最大窗口 {max_window}")

    # 2. 时序连续性（相邻 bar 日期间隔合理）
    # P0-6 修复（2026-08-20 双盲审计 F3.3）：阈值 7 天把春节(8-10 自然日)/国庆长假全判断点——
    # 跨任何长假的回测整体 failed。15 天=覆盖最长法定假期+缓冲；真断点（停牌数月/数据缺失）仍拦。
    ts_list = [b.get("ts") for b in bars if b.get("ts")]
    gaps = 0
    for i in range(1, len(ts_list)):
        try:
            prev = ts_list[i-1]
            curr = ts_list[i]
            if hasattr(prev, "date") and hasattr(curr, "date"):
                diff = (curr.date() - prev.date()).days
                if diff > 15:  # 日线超过 15 天间隔判定为断点（长假豁免）
                    gaps += 1
        except Exception as e:
            logger.warning("时序连续性检查异常: %s", e)
    if gaps > 0:
        issues.append(f"时序断点 {gaps} 处")
    else:
        checks.append(f"✓ 时序连续无断点")

    # 3. 价量为 0 检查
    zero_count = sum(1 for b in bars if b.get("close", 0) == 0 or b.get("volume", 0) == 0)
    if zero_count > 0:
        issues.append(f"价/量为 0 的 bar: {zero_count} 条")
    else:
        checks.append(f"✓ 无价/量为 0 数据")

    # 4. 品类兼容性
    from .factor import validate_strategy_factors
    v = validate_strategy_factors(config.symbol, config.factors)
    if not v["valid"]:
        issues.append(f"因子品类不兼容: {v['message']}")
    else:
        checks.append(f"✓ 因子品类兼容 ({v['category']})")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "checks": checks,
        "bars_count": len(bars),
        "max_window": max_window,
    }
