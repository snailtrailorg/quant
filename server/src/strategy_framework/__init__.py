"""策略/模型统一框架 -- 跨三市场统一的策略抽象。"""

from .factor import Factor, BarContext, DSLFactor, register_factor, list_factors, get_factor
from .strategy import Strategy, StrategyConfig, Signal, Action, SignalAggregator
from .adapters import (ExecutionAdapter, XTPAdapter,
                       CryptoPerpAdapter, create_adapter, Order, Position)
from .backtest import BacktestEngine, BacktestResult, BacktestAdapter, Trade

__all__ = [
    "Factor", "BarContext", "DSLFactor", "register_factor", "list_factors", "get_factor",
    "Strategy", "StrategyConfig", "Signal", "Action", "SignalAggregator",
    "ExecutionAdapter", "XTPAdapter", "CryptoPerpAdapter",
    "create_adapter", "Order", "Position",
    "BacktestEngine", "BacktestResult", "BacktestAdapter", "Trade",
]
