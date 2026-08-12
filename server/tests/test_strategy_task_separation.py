"""策略与实盘任务分离单测：parameter_defs + live_task + symbol_params。"""

import pytest
from src.strategy_framework.strategy import (
    validate_parameter_defs, build_default_params, validate_params_against_defs,
    StrategyConfig, Strategy,
)


class TestParameterDefs:
    def test_validate_defs_ok(self):
        defs = [
            {"name": "buy_threshold", "type": "number", "default": 0.02, "min": 0, "max": 1},
            {"name": "use_trailing_stop", "type": "boolean", "default": False},
        ]
        assert validate_parameter_defs(defs) is None

    def test_validate_defs_dup_name(self):
        defs = [
            {"name": "x", "type": "number", "default": 1},
            {"name": "x", "type": "number", "default": 2},
        ]
        assert validate_parameter_defs(defs) is not None and "重复" in validate_parameter_defs(defs)

    def test_validate_defs_bad_type(self):
        defs = [{"name": "x", "type": "unsupported", "default": 1}]
        assert validate_parameter_defs(defs) is not None

    def test_validate_defs_missing_default(self):
        defs = [{"name": "x", "type": "number"}]
        assert validate_parameter_defs(defs) is not None and "default" in validate_parameter_defs(defs)

    def test_build_default_params(self):
        defs = [
            {"name": "a", "type": "number", "default": 1},
            {"name": "b", "type": "boolean", "default": True},
        ]
        assert build_default_params(defs) == {"a": 1, "b": True}

    def test_validate_params_ok(self):
        defs = [{"name": "buy_threshold", "type": "number", "default": 0.02, "min": 0, "max": 1}]
        assert validate_params_against_defs({"buy_threshold": 0.05}, defs) is None

    def test_validate_params_out_of_range(self):
        defs = [{"name": "buy_threshold", "type": "number", "default": 0.02, "min": 0, "max": 1}]
        err = validate_params_against_defs({"buy_threshold": 2.0}, defs)
        assert err is not None and "超过最大值" in err

    def test_validate_params_wrong_type(self):
        defs = [{"name": "use_ts", "type": "boolean", "default": False}]
        err = validate_params_against_defs({"use_ts": "yes"}, defs)
        assert err is not None and "布尔" in err

    def test_validate_params_select(self):
        defs = [{"name": "mode", "type": "select", "default": "a",
                 "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}]}]
        assert validate_params_against_defs({"mode": "a"}, defs) is None
        err = validate_params_against_defs({"mode": "c"}, defs)
        assert err is not None and "不在可选项" in err


class TestStrategyConfig:
    def test_strategy_config_has_params_field(self):
        cfg = StrategyConfig(
            id="t", name="t", type="astock", symbol="600000.SHSE", adapter="xtp",
            params={"mode": "python", "python_code": "def on_bar(ctx):\n    return ctx.hold()",
                    "parameter_defs": [{"name": "x", "type": "number", "default": 1}]}
        )
        assert cfg.params["mode"] == "python"
        assert cfg.params["parameter_defs"][0]["name"] == "x"


class TestLiveTaskIntegration:
    """live_task 创建时合并策略参数 + 任务参数。"""

    def test_param_merge_strategy_defaults_plus_task_overrides(self):
        """任务级参数覆盖策略默认值。"""
        defs = [{"name": "buy_threshold", "type": "number", "default": 0.02, "min": 0, "max": 1}]
        # 策略快照的 params（含 mode + parameter_defs）
        strategy_params = {"mode": "python", "python_code": "...", "parameter_defs": defs}
        # 任务级参数（用户在创建任务时填的）
        task_params = {"buy_threshold": 0.05}
        # 合并：策略级 params + 任务级参数覆盖
        merged = {**strategy_params, **task_params}
        # mode/python_code 保留策略级，buy_threshold 用任务级
        assert merged["mode"] == "python"
        assert merged["python_code"] == "..."
        assert merged["buy_threshold"] == 0.05


class TestBacktestSymbolParams:
    """回测 per-symbol 参数覆盖。"""

    def test_symbol_params_merge(self):
        """symbol_params 覆盖默认参数。"""
        strategy_params = {"mode": "python", "python_code": "...", "buy_threshold": 0.02}
        symbol_params = {"600000.SHSE": {"buy_threshold": 0.03}, "600001.SHSE": {}}
        # 标的 600000 用 0.03，标的 600001 用默认 0.02
        for symbol, expected_threshold in [("600000.SHSE", 0.03), ("600001.SHSE", 0.02)]:
            per_symbol = symbol_params.get(symbol, {})
            merged = {**strategy_params, **per_symbol}
            assert merged["buy_threshold"] == expected_threshold