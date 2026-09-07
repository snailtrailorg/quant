"""策略/模型统一框架 -- 跨三市场统一的策略抽象。

批 9 内存治理（2026-09-08）：四组 re-export 惰性化（PEP 562）——backtest.py 的
numpy/pandas 不再随包 import 进入 beat/调度链。包级用法面已核全为函数级 import
（strategies/*.py 取 BarContext），首次使用时照常加载，行为等价；worker/回测
路径不受影响。
"""

from importlib import import_module

_REEXPORTS: dict[str, str] = {
    # factor（轻量：ast/operator）
    "Factor": ".factor", "BarContext": ".factor", "DSLFactor": ".factor",
    "register_factor": ".factor", "list_factors": ".factor", "get_factor": ".factor",
    "_check_ast_blacklist": ".factor", "load_factors_from_db": ".factor",
    "_make_factor_class": ".factor", "register_custom_factor": ".factor",
    "delete_custom_factor": ".factor", "_FACTOR_REGISTRY": ".factor",
    # strategy（轻量：enum/dataclass；.factor/.broker 同轻）
    "Strategy": ".strategy", "StrategyConfig": ".strategy", "Signal": ".strategy",
    "Action": ".strategy", "SignalAggregator": ".strategy", "PythonStrategy": ".strategy",
    "StrategyContext": ".strategy", "validate_parameter_defs": ".strategy",
    "build_default_params": ".strategy", "validate_params_against_defs": ".strategy",
    # adapters（轻量：abc/threading）
    "ExecutionAdapter": ".adapters", "XTPAdapter": ".adapters",
    "CryptoPerpAdapter": ".adapters", "create_adapter": ".adapters",
    "Order": ".adapters", "Position": ".adapters",
    # backtest（重：numpy/pandas——惰性化的目标）
    "BacktestEngine": ".backtest", "BacktestResult": ".backtest",
    "BacktestAdapter": ".backtest", "Trade": ".backtest",
}


def __getattr__(name: str):
    if name in _REEXPORTS:
        return getattr(import_module(_REEXPORTS[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_REEXPORTS))


__all__ = [
    "Factor", "BarContext", "DSLFactor", "register_factor", "list_factors", "get_factor",
    "Strategy", "StrategyConfig", "Signal", "Action", "SignalAggregator",
    "PythonStrategy", "StrategyContext", "_check_ast_blacklist",
    "ExecutionAdapter", "XTPAdapter", "CryptoPerpAdapter",
    "create_adapter", "Order", "Position",
    "BacktestEngine", "BacktestResult", "BacktestAdapter", "Trade",
]
