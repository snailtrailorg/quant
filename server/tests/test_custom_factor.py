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

class TestDslFactorRegister:
    """web 长尾批（2026-09-01，13号#2）：DSL 因子 register/load 校验面。"""

    def test_validate_dsl_expr_window_and_plain(self):
        """窗口表达式返回最大 n；纯算术返回 1（最小历史）。"""
        from src.strategy_framework.factor import validate_dsl_expr
        assert validate_dsl_expr("mean(close,20) / close - 1") == 20
        assert validate_dsl_expr("max(high, 5) - min(low, 3)") == 5   # 取最大窗
        assert validate_dsl_expr("close / high") == 1

    def test_validate_dsl_expr_rejects(self):
        """坏表达式 register 期 ValueError（盲审 A/B-P0：原构造零校验实盘才爆）。"""
        from src.strategy_framework.factor import validate_dsl_expr
        for bad in ["mean(close,20) /",            # 语法错
                    "avg(close,20)",               # 未知函数
                    "mean(mean(close,2),3)",       # 窗口嵌套
                    "foo(close,20)",               # 未知函数
                    "mean(bar,5)",                 # 未知字段
                    "x + 1"]:                      # 未知变量
            with pytest.raises(ValueError):
                validate_dsl_expr(bad)

    def test_register_rejects_dsl_prefix_name(self):
        """dsl: 前缀名拒绝（strategy.py:175 内联路径劫持，盲审 B-P2）。"""
        from src.strategy_framework.factor import register_custom_factor
        with pytest.raises(ValueError, match="dsl:"):
            register_custom_factor("dsl:evil", "custom", "close")

    def test_register_dsl_validates_before_db(self):
        """ftype=dsl 坏表达式在 DB 写入前 ValueError（不入库）。"""
        from src.strategy_framework.factor import register_custom_factor
        with pytest.raises(ValueError, match="DSL"):
            register_custom_factor("bad_dsl", "custom", "not an expression!", ftype="dsl")

    def test_load_dsl_registers_partial(self):
        """load_factors_from_db 的 dsl 分流：partial 注册零参可调+needs_history=窗口 n。"""
        from unittest.mock import patch, MagicMock
        from src.strategy_framework.factor import load_factors_from_db, _FACTOR_REGISTRY
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.return_value = [
            ("dslma", "custom", "d", "mean(close,20) / close - 1", "{}", 0, "dsl"),
            ("pyma", "custom", "d", "def compute(ctx):\n    return ctx.close", "{}", 5, "python"),
            ("baddsl", "custom", "d", "oops(", "{}", 0, "dsl"),
        ]
        with patch("src.data_platform.db.get_conn", return_value=conn):
            loaded = load_factors_from_db()
        assert set(loaded) == {"dslma", "pyma"}          # 坏表达式跳过不炸
        e = _FACTOR_REGISTRY["dslma"]
        assert e["type"] == "dsl" and e["needs_history"] == 20
        inst = e["cls"]()                                  # 零参调用语义（strategy.py 消费面）
        assert inst.expr == "mean(close,20) / close - 1"
        assert _FACTOR_REGISTRY["pyma"]["type"] == "python"
        assert "baddsl" not in _FACTOR_REGISTRY
        del _FACTOR_REGISTRY["dslma"], _FACTOR_REGISTRY["pyma"]


class TestDslPreview:
    """W1（2026-09-01）：preview 端点 dsl 分支——短窗拉满/坏表达式 error/非法 type 400。"""

    def _bars_df(self, n=150):
        import pandas as pd
        from datetime import datetime, timedelta
        base = datetime(2026, 8, 1)
        return pd.DataFrame([{
            "ts": base + timedelta(days=i), "open": 10.0, "high": 10.5,
            "low": 9.5, "close": 10.0 + i * 0.01, "volume": 1000,
        } for i in range(n)])

    def test_preview_dsl_window_autoraise(self):
        """mean(close,120)：请求缺省 bars=60 → 自动拉满 120 根（盲审 B-P1 短窗静默错值根修）。"""
        from unittest.mock import patch
        from src.web_api.routes.strategy import preview_factor_api
        with patch("src.data_platform.db.get_bars", return_value=self._bars_df(150)):
            out = preview_factor_api({"type": "dsl", "code": "mean(close,120) / close - 1",
                                      "symbol": "600000.SHSE", "freq": "1D"}, {})
        assert len(out["values"]) == 120
        assert out["stats"]["errors"] == 0 and out["stats"]["last"] is not None
        assert out["stats"]["last"] < 0   # 递增序列:均值<当前 → 偏离度为负(数值正确性)

    def test_preview_dsl_bad_expr_error_not_500(self):
        from unittest.mock import patch
        from src.web_api.routes.strategy import preview_factor_api
        with patch("src.data_platform.db.get_bars", return_value=self._bars_df()):
            out = preview_factor_api({"type": "dsl", "code": "avg(close,20)"}, {})
        assert "error" in out and "avg" in out["error"]

    def test_preview_bad_type_400(self):
        import pytest
        from src.web_api.errors import ApiError
        from src.web_api.routes.strategy import preview_factor_api
        with pytest.raises(ApiError) as ei:
            preview_factor_api({"type": "js", "code": "close"}, {})
        assert ei.value.code == "FACTOR_PREVIEW_TYPE"   # 盲审 A-P2：断错误码非仅类型

    def test_preview_dsl_window_over_limit_error(self):
        """窗口>500：显式 error 不截断（盲审 A/B-P1：截断=短窗错值换门回归）。"""
        from unittest.mock import patch
        from src.web_api.routes.strategy import preview_factor_api
        with patch("src.data_platform.db.get_bars", return_value=self._bars_df(600)):
            out = preview_factor_api({"type": "dsl", "code": "mean(close,600) / close - 1"}, {})
        assert "error" in out and "500" in out["error"]

    def test_preview_dsl_insufficient_data_error(self):
        """数据不足以拉满窗口：显式 error（1D 取数窗 365 天≈243 根遇 mean(close,300) 场景）。"""
        from unittest.mock import patch
        from src.web_api.routes.strategy import preview_factor_api
        with patch("src.data_platform.db.get_bars", return_value=self._bars_df(100)):
            out = preview_factor_api({"type": "dsl", "code": "mean(close,120) / close - 1"}, {})
        assert "error" in out and "120" in out["error"]
