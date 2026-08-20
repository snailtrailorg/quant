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
    """选股引擎（2026-08-20 横截面重写）：本地一档表批量，注册表因子。"""

    def test_selection_factors_registry(self):
        """横截面因子注册表：配置驱动（weight/direction/col 三键齐）。"""
        from src.astock_analysis.analysis import SELECTION_FACTORS
        for name, spec in SELECTION_FACTORS.items():
            assert {"weight", "direction", "col"} <= set(spec), f"{name} 配置缺键"
            assert spec["direction"] in (1, -1)

    def _fake_conn(self, monkeypatch, df):
        """G6：mock get_conn（非 pd.read_sql）——隔离真 DB，连无 PG 环境可跑。"""
        import pandas as pd

        class _Cur:
            def __init__(self):
                from types import SimpleNamespace
                self.description = [SimpleNamespace(name=c) for c in df.columns]
            def execute(self, *a, **k): return self
            def fetchall(self): return list(df.itertuples(index=False, name=None))
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class _Conn:
            def cursor(self): return _Cur()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        from src.data_platform import db as _db
        monkeypatch.setattr(_db, "get_conn", lambda: _Conn())

    def test_selection_missing_column_skipped(self, monkeypatch):
        """数据面缺列（如 cyq_perf 未同步）→ 该因子中性跳过不炸。"""
        import pandas as pd
        n = 100
        df = pd.DataFrame({
            "ts_code": [f"{i:06d}.SH" for i in range(n)],
            "name": [f"股{i}" for i in range(n)],
            "industry": ["行业"] * n,
            "close": [10.0 + i * 0.1 for i in range(n)],
            "turnover_rate": [1.0] * n, "total_mv": [100.0] * n, "circ_mv": [80.0] * n,
            "ma_dev": [i / n for i in range(n)],
            "lo20": [9.0] * n, "hi20": [11.0] * n,
            "net_mf_pct": [None] * n,      # 缺
            "lg_flow_pct": [0.001] * n,
            "winner_rate": [None] * n,     # 缺
        })
        self._fake_conn(monkeypatch, df)
        from src.astock_analysis.analysis import DailySelectionEngine
        r = DailySelectionEngine(top_n=5).run()
        assert 0 < len(r) <= 5
        assert r[0].factors["net_mf_pct"] is None       # 缺=中性
        assert r[0].factors["lg_flow_pct"] is not None  # 在
        assert r[0].score >= r[-1].score                # 降序

    def test_selection_row_level_missing_factor(self, monkeypatch):
        """O 审 S1：个别行缺因子（LEFT JOIN 落空）→ 行级权重重分配，不 NaN 沉底。

        构造：50 行，其中 1 行 net_mf_pct=None 但 ma_dev 全场最优——该行 score 应
        为可用因子的加权均值（非 NaN），且因 ma_dev 占优排名靠前。
        """
        import pandas as pd
        n = 50
        df = pd.DataFrame({
            "ts_code": [f"{i:06d}.SH" for i in range(n)],
            "name": [f"股{i}" for i in range(n)],
            "industry": ["行业"] * n,
            "close": [10.0] * n, "turnover_rate": [1.0] * n,
            "total_mv": [100.0] * n, "circ_mv": [80.0] * n,
            "ma_dev": [0.0] * (n - 1) + [10.0],        # 最后一行全场最优
            "net_mf_pct": [0.001] * (n - 1) + [None],  # 该行恰缺此因子
            "lg_flow_pct": [0.0] * n, "winner_rate": [0.5] * n,
            "lo20": [9.0] * n, "hi20": [11.0] * n,
        })
        self._fake_conn(monkeypatch, df)
        from src.astock_analysis.analysis import DailySelectionEngine
        r = DailySelectionEngine(top_n=10).run()
        top3 = [x.symbol for x in r[:3]]
        assert f"{n-1:06d}.SH" in top3, "行级缺因子的最优标的不应被 NaN 沉底"
        assert all(pd.notna(x.score) for x in r)

    def test_selection_rating_has_discrimination(self, monkeypatch):
        """O 审 G4：rating 分位在 top_n 内算——top 内应有 BUY/HOLD/AVOID 区分。"""
        import pandas as pd
        n = 200
        df = pd.DataFrame({
            "ts_code": [f"{i:06d}.SH" for i in range(n)],
            "name": [f"股{i}" for i in range(n)], "industry": ["行业"] * n,
            "close": [10.0] * n, "turnover_rate": [1.0] * n,
            "total_mv": [100.0] * n, "circ_mv": [80.0] * n,
            "ma_dev": [i / n for i in range(n)],
            "net_mf_pct": [i / n for i in range(n)],
            "lg_flow_pct": [i / n for i in range(n)], "winner_rate": [i / n for i in range(n)],
            "lo20": [9.0] * n, "hi20": [11.0] * n,
        })
        self._fake_conn(monkeypatch, df)
        from src.astock_analysis.analysis import DailySelectionEngine
        r = DailySelectionEngine(top_n=20).run()
        ratings = {x.rating for x in r}
        assert "BUY" in ratings and "AVOID" in ratings, f"top 内应区分: {ratings}"

    def test_selection_no_usable_factor_returns_empty(self, monkeypatch):
        """全部因子缺数 → 空结果不炸。"""
        import pandas as pd
        n = 50
        df = pd.DataFrame({
            "ts_code": [f"{i:06d}.SH" for i in range(n)], "name": ["x"] * n,
            "industry": ["i"] * n, "close": [10.0] * n, "turnover_rate": [1.0] * n,
            "total_mv": [100.0] * n, "circ_mv": [80.0] * n,
            "ma_dev": [None] * n, "lo20": [9.0] * n, "hi20": [11.0] * n,
            "net_mf_pct": [None] * n, "lg_flow_pct": [None] * n, "winner_rate": [None] * n,
        })
        self._fake_conn(monkeypatch, df)
        from src.astock_analysis.analysis import DailySelectionEngine
        assert DailySelectionEngine().run() == []
