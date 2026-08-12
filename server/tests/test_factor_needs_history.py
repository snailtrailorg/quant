"""因子静态/动态区分单测（needs_history）。"""

import pytest
from src.strategy_framework.factor import list_factors, get_factor, register_factor


class TestNeedsHistory:
    def test_preset_factors_have_needs_history(self):
        """预置因子都标注了 needs_history。"""
        for f in list_factors():
            assert "needs_history" in f, f"{f['name']} 缺 needs_history 字段"

    def test_preset_factors_correct_needs_history(self):
        """预置因子 needs_history 值正确。"""
        expected = {
            "ma_dev": 20,
            "rsi": 14,
            "volume_ratio": 5,
            "double_low": 0,
            "funding_rate": 0,
            "dsl": 0,
        }
        for name, nh in expected.items():
            entry = get_factor(name)
            assert entry is not None, f"因子 {name} 未注册"
            assert entry["needs_history"] == nh, f"{name}: expected needs_history={nh}, got {entry['needs_history']}"

    def test_static_only_filter(self):
        """static_only=True 只返回 needs_history=0 的因子。"""
        static = list_factors(static_only=True)
        for f in static:
            assert f["needs_history"] == 0, f"{f['name']} 不是静态因子但被 static_only 返回"
        # 静态因子包含 double_low/funding_rate/dsl
        names = [f["name"] for f in static]
        assert "double_low" in names
        assert "funding_rate" in names
        assert "dsl" in names

    def test_static_only_excludes_dynamic(self):
        """static_only=True 不含动态因子。"""
        static = list_factors(static_only=True)
        names = [f["name"] for f in static]
        assert "ma_dev" not in names
        assert "rsi" not in names
        assert "volume_ratio" not in names

    def test_dynamic_factors_have_positive_needs_history(self):
        """动态因子 needs_history > 0。"""
        for f in list_factors():
            if f["name"] in ("ma_dev", "rsi", "volume_ratio"):
                assert f["needs_history"] > 0, f"{f['name']} 应是动态因子"

    def test_category_filter_combined_with_static_only(self):
        """category + static_only 组合过滤。"""
        static_trend = list_factors(category="trend", static_only=True)
        for f in static_trend:
            assert f["category"] == "trend"
            assert f["needs_history"] == 0


class TestSelectionEngineStaticOnly:
    """选股引擎只用静态因子。"""

    def test_selection_engine_uses_static_factors_only(self):
        """选股引擎 _factors 只含 needs_history=0 的因子。"""
        from src.astock_analysis.analysis import DailySelectionEngine
        engine = DailySelectionEngine()
        for f in engine._factors:
            assert f["needs_history"] == 0, f"选股引擎含动态因子 {f['name']}"
