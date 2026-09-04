"""ptrade 全家桶批 1 · 基准指标黄金用例测试（2026-09-04）。

验收可验性（方案定稿十）：构造已知 β 的合成序列，手算 α/β 断言数值，防公式错。
"""
import numpy as np


def test_align_benchmark_returns_beta_one():
    """黄金用例 1：策略完全跟踪基准（β=1）→ α=0。"""
    from src.strategy_framework.backtest import _align_benchmark_returns
    benchmark = [{"ts": f"2024-01-{i:02d}", "close": 100 * (1.01 ** (i - 1))} for i in range(1, 6)]
    daily = [{"ts": f"2024-01-{i:02d}", "value": 100 * (1.01 ** (i - 1))} for i in range(1, 6)]
    r_p, r_b = _align_benchmark_returns(daily, benchmark)
    assert len(r_p) == 4 and len(r_b) == 4
    beta = np.cov(r_p, r_b, ddof=0)[0, 1] / np.var(r_b, ddof=0)
    assert abs(beta - 1.0) < 1e-9
    rf = 0.02 / 252
    alpha = (np.mean(r_p) - rf - beta * (np.mean(r_b) - rf)) * 252
    assert abs(alpha) < 1e-9


def test_align_benchmark_returns_constant_benchmark():
    """黄金用例 2：基准恒定（β 不可算）→ r_b 全 0。"""
    from src.strategy_framework.backtest import _align_benchmark_returns
    benchmark = [{"ts": f"2024-01-{i:02d}", "close": 100} for i in range(1, 6)]
    daily = [{"ts": f"2024-01-{i:02d}", "value": 100 * (1.01 ** (i - 1))} for i in range(1, 6)]
    r_p, r_b = _align_benchmark_returns(daily, benchmark)
    assert all(abs(x) < 1e-12 for x in r_b)
    assert np.var(r_b, ddof=0) == 0


def test_align_benchmark_returns_date_alignment():
    """黄金用例 3：日期对齐 inner join，策略缺日跳过。"""
    from src.strategy_framework.backtest import _align_benchmark_returns
    benchmark = [{"ts": f"2024-01-{i:02d}", "close": 100} for i in range(1, 5)]
    daily = [{"ts": "2024-01-01", "value": 100},
             {"ts": "2024-01-03", "value": 101},
             {"ts": "2024-01-04", "value": 102}]
    r_p, r_b = _align_benchmark_returns(daily, benchmark)
    assert len(r_p) == 2   # 公共日期 01/03/04 → 2 个收益率（01→03, 03→04）


def test_rolling_metrics_monthly_windows():
    """滚动绩效：每月 × 1/3/6/12 窗口，窗口不足 N 月返回 None，指标字段齐全。"""
    from src.strategy_framework.backtest import rolling_metrics
    daily = [
        {"ts": "2026-01-05", "value": 100.0},
        {"ts": "2026-01-30", "value": 101.0},
        {"ts": "2026-02-05", "value": 102.0},
        {"ts": "2026-02-27", "value": 103.0},
        {"ts": "2026-03-05", "value": 104.0},
        {"ts": "2026-03-31", "value": 105.0},
    ]
    result = rolling_metrics(daily, [])
    assert set(result.keys()) == {"2026-01", "2026-02", "2026-03"}
    # 早期窗口：1 月有数据，3/6/12 月不足 None
    assert result["2026-01"]["1"] is not None
    assert result["2026-01"]["3"] is None
    assert result["2026-01"]["6"] is None
    assert result["2026-01"]["12"] is None
    # 3 月窗口（1-3 月）有数据，6/12 不足
    assert result["2026-03"]["3"] is not None
    assert result["2026-03"]["6"] is None
    # 指标字段齐全
    m = result["2026-03"]["3"]
    for k in ("return", "max_drawdown", "sharpe", "sortino", "alpha", "beta",
              "information_ratio", "volatility", "benchmark_return", "benchmark_volatility"):
        assert k in m
    # 3 月窗口收益 = (105/100 - 1)*100 = 5.0
    assert m["return"] == 5.0
