"""因子平台化单测：自定义因子创建/加载/DSL 调用/Python 调用。"""

import pytest
from src.strategy_framework.factor import (
    _check_ast_blacklist, _make_factor_class, list_factors, get_factor,
    _FACTOR_REGISTRY, register_factor,
)
from src.strategy_framework.strategy import Strategy, StrategyConfig, StrategyContext, PythonStrategy


class TestCustomFactor:
    def test_make_factor_class_safe_code(self):
        """安全代码编译为 Factor 子类。"""
        code = 'def compute(ctx, n=20):\n    return ctx.close / 10 - 1'
        cls = _make_factor_class('test_safe', code, {'n': 20})
        assert cls.__name__ == 'CustomFactor_test_safe'

    def test_make_factor_class_import_blocked(self):
        """import 应被拒绝。"""
        code = 'import os\ndef compute(ctx):\n    return ctx.close'
        with pytest.raises(ValueError, match="安全校验失败"):
            _make_factor_class('test_import', code, {})

    def test_make_factor_class_exec_blocked(self):
        """exec 应被拒绝。"""
        code = 'def compute(ctx):\n    exec("x")\n    return ctx.close'
        with pytest.raises(ValueError, match="安全校验失败"):
            _make_factor_class('test_exec', code, {})

    def test_make_factor_class_no_compute_raises(self):
        """没有 compute 函数应报错。"""
        code = 'def other_func():\n    return 1'
        with pytest.raises(ValueError, match="必须定义 compute"):
            _make_factor_class('test_nocompute', code, {})

    def test_make_factor_class_compute_works(self):
        """编译后的因子 compute 能正确执行。"""
        code = 'def compute(ctx, n=20):\n    closes = [h.get("close", 0) for h in ctx.history[-n:]] + [ctx.close]\n    sma = sum(closes) / len(closes)\n    return ctx.close / sma - 1'
        cls = _make_factor_class('test_compute', code, {'n': 20})
        factor = cls()
        from src.strategy_framework.factor import BarContext
        ctx = BarContext(close=10.0, high=11.0, low=9.0, open_=9.8, volume=1000,
                         history=[{'close': 9.0}, {'close': 9.5}, {'close': 9.8}])
        val = factor.compute(ctx)
        # sma of [9.0, 9.5, 9.8, 10.0] = 9.575, 10/9.575 - 1 ≈ 0.0444
        assert abs(val - 0.0444) < 0.01

    def test_make_factor_class_syntax_error(self):
        """语法错误应报错。"""
        code = 'def compute(ctx):\n    syntax error here'
        with pytest.raises(ValueError, match="安全校验失败"):
            _make_factor_class('test_syntax', code, {})

    def test_preset_factors_still_registered(self):
        """预置因子不回归。"""
        names = [f['name'] for f in list_factors()]
        assert 'ma_dev' in names
        assert 'rsi' in names
        assert 'volume_ratio' in names
        assert 'double_low' in names
        assert 'funding_rate' in names
        assert 'dsl' in names

    def test_preset_factors_not_custom(self):
        """预置因子 is_custom=False。"""
        for f in list_factors():
            if f['name'] in ('ma_dev', 'rsi', 'volume_ratio', 'double_low', 'funding_rate', 'dsl'):
                assert f['is_custom'] is False, f"{f['name']} should not be custom"


class TestPythonModeGetFactor:
    """#15 Python 模式策略调 ctx.get_factor()。"""

    def test_python_strategy_calls_preset_factor(self):
        """Python 模式策略调预置因子。"""
        code = '''def on_bar(ctx):
    ma_dev = ctx.get_factor("ma_dev", n=20)
    if ma_dev > 0.02:
        return ctx.buy(100)
    return ctx.hold()
'''
        cfg = StrategyConfig(
            id='test', name='test', type='python', symbol='600000.SHSE',
            adapter='xtp', params={'mode': 'python', 'python_code': code}
        )
        strat = Strategy.from_config(cfg, None)
        # close > sma*1.02 -> ma_dev > 0.02 -> BUY
        sig = strat.on_bar({'close': 12.0, 'high': 12.0, 'low': 9.0, 'volume': 1000},
                           [{'close': 9.0}, {'close': 9.5}, {'close': 9.8}])
        assert sig is not None
        assert sig.action.name == 'BUY'

    def test_python_strategy_calls_unknown_factor_raises(self):
        """调不存在的因子应抛 ValueError。"""
        code = '''def on_bar(ctx):
    val = ctx.get_factor("nonexistent_factor")
    return ctx.hold()
'''
        cfg = StrategyConfig(
            id='test2', name='test2', type='python', symbol='600000.SHSE',
            adapter='xtp', params={'mode': 'python', 'python_code': code}
        )
        strat = Strategy.from_config(cfg, None)
        # 用户代码内部抛 ValueError，on_bar 应捕获异常返回 None（compute_factors 会 catch）
        # 但 get_factor 直接 raise，用户代码没 try，会向上抛
        # 这里测试 get_factor 本身的行为
        ctx = StrategyContext('test', 's')
        ctx._update({'close': 10.0}, [], {})
        with pytest.raises(ValueError, match="未知因子"):
            ctx.get_factor("nonexistent_factor")