"""策略/模型统一框架 -- 跨三市场统一的策略抽象。"""

from .factor import (Factor, BarContext, DSLFactor, register_factor, list_factors, get_factor,
                      _check_ast_blacklist, load_factors_from_db, _make_factor_class,
                      register_custom_factor, delete_custom_factor, _FACTOR_REGISTRY)
from .strategy import (Strategy, StrategyConfig, Signal, Action, SignalAggregator,
                        PythonStrategy, StrategyContext,
                        validate_parameter_defs, build_default_params, validate_params_against_defs)
from .adapters import (ExecutionAdapter, XTPAdapter,
                       CryptoPerpAdapter, create_adapter, Order, Position)
from .backtest import BacktestEngine, BacktestResult, BacktestAdapter, Trade

__all__ = [
    "Factor", "BarContext", "DSLFactor", "register_factor", "list_factors", "get_factor",
    "Strategy", "StrategyConfig", "Signal", "Action", "SignalAggregator",
    "PythonStrategy", "StrategyContext", "_check_ast_blacklist",
    "ExecutionAdapter", "XTPAdapter", "CryptoPerpAdapter",
    "create_adapter", "Order", "Position",
    "BacktestEngine", "BacktestResult", "BacktestAdapter", "Trade",
]
