"""#15 Python 代码框 · 单测。"""

import pytest
from src.strategy_framework.strategy import (
    _check_ast_blacklist, StrategyContext, PythonStrategy, StrategyConfig, Strategy
)


class TestPythonStrategy:
    def test_ast_safe_code(self):
        """安全代码应通过 AST 校验。"""
        assert _check_ast_blacklist('def on_bar(ctx):\n    return ctx.hold()') is None

    def test_ast_import_blocked(self):
        """import 应被拦截。"""
        assert _check_ast_blacklist('import os') is not None

    def test_ast_from_import_blocked(self):
        """from ... import 应被拦截。"""
        assert _check_ast_blacklist('from os import system') is not None

    def test_ast_exec_blocked(self):
        """exec 应被拦截。"""
        assert _check_ast_blacklist('exec("x")') is not None

    def test_ast_open_blocked(self):
        """open 应被拦截。"""
        assert _check_ast_blacklist('open("/etc/passwd")') is not None

    def test_ast_ctx_buy_allowed(self):
        """ctx.buy() 应允许。"""
        assert _check_ast_blacklist('def on_bar(ctx):\n    return ctx.buy(100)') is None

    def test_ast_syntax_error(self):
        """语法错误应捕获。"""
        assert _check_ast_blacklist('def on_bar(ctx):\n    syntax error') is not None

    def test_from_config_returns_python_strategy(self):
        """mode=python 时 from_config 返回 PythonStrategy。"""
        cfg = StrategyConfig(
            id='test', name='test', type='python', symbol='600000.SHSE',
            adapter='xtp',
            params={'mode': 'python', 'python_code': 'def on_bar(ctx):\n    return ctx.hold()'}
        )
        strat = Strategy.from_config(cfg, None)
        assert isinstance(strat, PythonStrategy)

    def test_on_bar_returns_hold(self):
        """PythonStrategy.on_bar 返回 HOLD 信号。"""
        cfg = StrategyConfig(
            id='test', name='test', type='python', symbol='600000.SHSE',
            adapter='xtp',
            params={'mode': 'python', 'python_code': 'def on_bar(ctx):\n    return ctx.hold()'}
        )
        strat = Strategy.from_config(cfg, None)
        sig = strat.on_bar({'close': 10.0, 'high': 11.0, 'low': 9.0, 'volume': 1000})
        assert sig is not None
        assert sig.action.name == 'HOLD'

    def test_state_management(self):
        """跨 tick 状态管理正常。"""
        cfg = StrategyConfig(
            id='test2', name='test2', type='python', symbol='600000.SHSE',
            adapter='xtp', params={
                'mode': 'python',
                'python_code': 'def on_bar(ctx):\n    count = ctx.get_state("count", 0)\n    ctx.set_state("count", count + 1)\n    if count >= 5:\n        return ctx.buy(100)\n    return ctx.hold()'
            }
        )
        strat = Strategy.from_config(cfg, None)
        for i in range(7):
            sig = strat.on_bar({'close': 10.0, 'high': 11.0, 'low': 9.0, 'volume': 1000})
            if i < 5:
                assert sig.action.name == 'HOLD', f'Iteration {i}: expected HOLD, got {sig.action}'
            else:
                assert sig.action.name == 'BUY', f'Iteration {i}: expected BUY, got {sig.action}'

    def test_strategy_context(self):
        """StrategyContext 方法正常。"""
        ctx = StrategyContext('test', '600000.SHSE')
        ctx._update({'close': 10.5, 'high': 11.0, 'low': 9.5, 'volume': 1000}, [], {})
        assert ctx.get_bar('close') == 10.5
        assert ctx.buy(200).action.name == 'BUY'
        assert ctx.sell(100).action.name == 'SELL'
        assert ctx.hold().action.name == 'HOLD'

    def test_get_history(self):
        """get_history 返回历史 close 值。"""
        ctx = StrategyContext('test', '600000.SHSE')
        hist = [{'close': 9.0}, {'close': 9.5}, {'close': 10.0}]
        ctx._update({'close': 10.5}, hist, {})
        closes = ctx.get_history(5)
        assert len(closes) == 3
        assert closes == [9.0, 9.5, 10.0]

    def test_get_param(self):
        """get_param 取策略参数。"""
        ctx = StrategyContext('test', '600000.SHSE')
        ctx._update({'close': 10.0}, [], {'threshold': 0.5})
        assert ctx.get_param('threshold') == 0.5
        assert ctx.get_param('nonexistent', 'default') == 'default'

    def test_sma_condition_buy(self):
        """SMA 条件判断 → BUY。"""
        cfg = StrategyConfig(
            id='test3', name='test3', type='python', symbol='600000.SHSE',
            adapter='xtp', params={
                'mode': 'python',
                'python_code': 'def on_bar(ctx):\n    hist = ctx.get_history(5)\n    if len(hist) >= 5:\n        sma = sum(hist) / len(hist)\n        c = ctx.get_bar("close")\n        if c > sma * 1.01:\n            return ctx.buy(100)\n    return ctx.hold()'
            }
        )
        strat = Strategy.from_config(cfg, None)
        hist = [{'close': 9.0}, {'close': 9.1}, {'close': 9.2}, {'close': 9.3}, {'close': 9.4}]
        sig = strat.on_bar({'close': 10.0, 'high': 10.0, 'low': 9.0, 'volume': 1000}, hist)
        assert sig is not None and sig.action.name == 'BUY'

    def test_dsl_mode_non_regression(self):
        """DSL 模式不回归。"""
        cfg = StrategyConfig(
            id='dsl-test', name='dsl-test', type='astock_analysis',
            symbol='000001.SHSE', adapter='xtp', params={},
            factors=[{'name': 'rsi', 'weight': 1.0, 'params': {'n': 14}}],
        )
        strat = Strategy.from_config(cfg, None)
        assert not isinstance(strat, PythonStrategy)