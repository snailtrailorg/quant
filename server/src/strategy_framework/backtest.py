"""回测引擎 -- 基于策略框架的标准回测，产出净值/胜率/回撤/夏普。

不依赖 VeighNa CtaTemplate，直接用我们的 Strategy 基类 + 历史 K 线。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable
import numpy as np
import pandas as pd

from .strategy import Strategy, StrategyConfig, Signal, Action, SignalAggregator
from .adapters import ExecutionAdapter, Order, Position


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
    # α/β/信息率/基准收益 TODO（需基准数据，后续基准接入后补）
    total_trades: int = 0          # 总交易次数
    # 明细
    daily_values: list = field(default_factory=list)   # 每日净值
    trades: list = field(default_factory=list)          # 成交记录
    metrics: dict = field(default_factory=dict)         # 全部指标


class BacktestAdapter(ExecutionAdapter):
    """回测适配器：订单按当前 bar 收盘价成交，记录交易。"""

    def __init__(self):
        self.trades: list[Trade] = []
        self._current_bar: dict = {}
        self._commission_rate: float = 0.0005  # 万五

    def set_bar(self, bar: dict):
        self._current_bar = bar

    def set_commission(self, rate: float):
        self._commission_rate = rate

    def send_order(self, order: Order) -> str:
        price = self._current_bar.get("close", 0)
        ts = self._current_bar.get("ts", datetime.now())
        commission = price * order.volume * self._commission_rate
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
            on_bar_callback: Callable | None = None) -> BacktestResult:
        """运行回测。

        Args:
            config: 策略配置
            bars: K 线列表 [{ts, open, high, low, close, volume, ...}, ...]
            shares_per_trade: 每笔交易股数
        """
        adapter = BacktestAdapter()
        adapter.set_commission(self.commission_rate)
        strategy = Strategy.from_config(config, adapter)

        # 回测数据完整性预检
        pc = precheck_backtest_data(config, bars)
        if not pc["valid"]:
            return BacktestResult(metrics={"error": "回测数据预检失败", "issues": pc["issues"]})

        # 防未来函数校验
        v = validate_no_future_data(strategy)
        if not v["valid"]:
            return BacktestResult(metrics={"error": "防未来函数校验失败", "details": v["warnings"]})

        # 回测模式：跳过风控（monkey-patch）
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
        strat_mod.Strategy.place_order = _bt_place_order

        try:
            cash = self.initial_capital
            position = 0
            avg_price = 0.0
            daily_values = []
            history: list[dict] = []  # 累积历史 K 线
            wins = 0
            total_closed = 0

            for i, bar in enumerate(bars):
                adapter.set_bar(bar)

                # 策略计算（传入历史）
                sig = strategy.on_bar(bar, history=history)
                # 当前 bar 入历史
                history.append(bar)

                # 处理成交
                for trade in adapter.trades:
                    if trade.ts == bar.get("ts") and trade.symbol == config.symbol:
                        if trade.action == "BUY":
                            cost = trade.price * trade.volume + trade.commission
                            if cash >= cost:
                                cash -= cost
                                total = position * avg_price + trade.volume * trade.price
                                position += trade.volume
                                avg_price = total / position if position > 0 else 0
                        elif trade.action == "SELL":
                            if position >= trade.volume:
                                proceeds = trade.price * trade.volume - trade.commission
                                cash += proceeds
                                # 胜负判定：卖出价 > 持仓均价
                                if trade.price > avg_price:
                                    wins += 1
                                total_closed += 1
                                position -= trade.volume
                                if position == 0:
                                    avg_price = 0.0

                # 每日净值
                close = bar.get("close", 0)
                portfolio_value = cash + position * close
                daily_values.append({
                    "ts": bar.get("ts"),
                    "cash": round(cash, 2),
                    "position": position,
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
            # 恢复
            strat_mod.Strategy.place_order = original_place_order

        # 计算指标
        return self._calculate(
            daily_values, adapter.trades, final_value,
            bars[0].get("ts") if bars else "",
            bars[-1].get("ts") if bars else "",
            wins, total_closed,
        )

    def _calculate(self, daily_values: list, trades: list[Trade],
                   final_value: float, start_ts, end_ts,
                   wins: int = 0, total_closed: int = 0) -> BacktestResult:
        """计算绩效指标。"""
        if not daily_values:
            return BacktestResult()

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
                "win_rate": round(win_rate, 1),
                "total_trades": len(trades),
                "commission_rate": self.commission_rate,
            },
        )

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
    ts_list = [b.get("ts") for b in bars if b.get("ts")]
    gaps = 0
    for i in range(1, len(ts_list)):
        try:
            prev = ts_list[i-1]
            curr = ts_list[i]
            if hasattr(prev, "date") and hasattr(curr, "date"):
                diff = (curr.date() - prev.date()).days
                if diff > 7:  # 日线超过 7 天间隔判定为断点
                    gaps += 1
        except Exception:
            pass
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
