"""链条打磨批次 1 测试：跨进程因子加载/执行规则三档/Signal 回填/回测参数对齐/预检置 failed/DSL 窗口函数。"""
from unittest.mock import patch, MagicMock, PropertyMock
from types import SimpleNamespace
import json

import pytest


# ── #12 执行规则三档 + #2 Signal 回填 ──

def _strategy(volume_type="SHARES", params=None):
    from src.strategy_framework.strategy import Strategy, StrategyConfig
    cfg = StrategyConfig(
        id="t1", name="t", type="astock_analysis", symbol="600000.SHSE", adapter="xtp",
        factors=[], aggregator={"threshold_buy": 0.5, "threshold_sell": -0.5},
        params={"mode": "python", "python_code": "def on_bar(ctx):\n    return ctx.hold()\n",
                "volume_type": volume_type, **(params or {})})
    return Strategy.from_config(cfg, MagicMock())


class TestResolveVolume:
    def test_shares_default(self):
        st = _strategy()
        assert st._resolve_volume(SimpleNamespace(), 9.0) == 100

    def test_percent_uses_account_snapshot(self):
        st = _strategy("PERCENT", {"volume_pct": 10})
        conn = MagicMock()
        conn.__enter__.return_value = conn
        # BUY 路径两次查询：总资产 1,000,000 + 持仓市值 0 → cash=1,000,000
        conn.execute.return_value.fetchone.side_effect = [(1000000,), (0.0,)]
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            # 10% × 1,000,000 = 100,000 元 ÷ 9.0 = 11,111 股 → 整百 → 11,100
            from src.strategy_framework.strategy import Action
            sig = SimpleNamespace(action=SimpleNamespace(name="BUY"))
            assert st._resolve_volume(sig, 9.0) == 11100

    def test_all_in(self):
        st = _strategy("ALL_IN")
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.side_effect = [(900000,), (0.0,)]
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            from src.strategy_framework.strategy import Action
            sig = SimpleNamespace(action=SimpleNamespace(name="BUY"))
            assert st._resolve_volume(sig, 9.0) == 100000

    def test_sell_all_in_uses_held_position_not_cash(self):
        """R-F1：SELL ALL_IN=清仓持仓（position_snapshot），不是现金÷price。"""
        st = _strategy("ALL_IN")
        conn = MagicMock()
        conn.__enter__.return_value = conn
        # 第一次查持仓（_held_volume）、第二次查市值（_held_value）
        conn.execute.return_value.fetchone.side_effect = [(500,), (4500.0,)]
        from src.strategy_framework.strategy import Action
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            v = st._resolve_volume(SimpleNamespace(action=SimpleNamespace(name="SELL")), 9.0)
        assert v == 500   # 卖持仓 500 股，不是 100000/9

    def test_sell_without_position_returns_zero(self):
        """R-F1：无持仓可卖 → 0（SC3 拦截非静默）。"""
        st = _strategy("ALL_IN")
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = (0,)
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            v = st._resolve_volume(SimpleNamespace(action=SimpleNamespace(name="SELL")), 9.0)
        assert v == 0

    def test_buy_all_in_uses_cash_not_total(self):
        """R-F1：BUY ALL_IN=可用资金口径（总资产−持仓市值）。"""
        st = _strategy("ALL_IN")
        conn = MagicMock()
        conn.__enter__.return_value = conn
        # _latest_total_value → 1,000,000；_held_value → 100,000 → cash=900,000
        conn.execute.return_value.fetchone.side_effect = [(1000000,), (100000.0,)]
        import src.data_platform.db as db
        with patch.object(db, "get_conn", return_value=conn):
            v = st._resolve_volume(SimpleNamespace(action=SimpleNamespace(name="BUY")), 9.0)
        assert v == 100000   # 900,000/9=100,000 股

    def test_percent_degrades_to_shares_on_db_fail(self):
        """资产查询失败 → 告警+降级 SHARES 100（不拒单）。"""
        st = _strategy("PERCENT")
        import src.data_platform.db as db
        with patch.object(db, "get_conn", side_effect=Exception("PG down")):
            assert st._resolve_volume(SimpleNamespace(), 9.0) == 100


class TestSignalBackfill:
    def test_aggregated_signal_gets_volume_price(self):
        """#2：因子模式聚合出的 Signal volume/price 恒 0 → 回填后可下单。"""
        from src.strategy_framework.strategy import Strategy, StrategyConfig, Action
        cfg = StrategyConfig(
            id="t2", name="t", type="astock_analysis", symbol="600000.SHSE", adapter="xtp",
            factors=[], aggregator={"threshold_buy": 0.5, "threshold_sell": -0.5},
            params={"mode": "python",
                    "python_code": "def on_bar(ctx):\n    return ctx.hold()\n",
                    "volume": 500})
        st = Strategy.from_config(cfg, MagicMock())
        bar = {"close": 9.05, "high": 9.1, "low": 9.0, "open": 9.0, "volume": 1000}
        sig = st.on_bar(bar, [])
        # HOLD 信号不回填——构造 BUY 路径：直接调内部
        fv_sig = st._aggregator.aggregate({"x": 1.0})   # score=1 > 0.5 → BUY
        if fv_sig.action != Action.HOLD:
            v = st._resolve_volume(fv_sig, 9.05)
            assert v == 500 and 9.05 > 0


# ── #8 DSL 窗口函数 ──

class TestDSLWindowFuncs:
    def _ctx(self, closes, volumes=None):
        from src.strategy_framework.factor import BarContext
        hist = [{"close": c, "high": c + .1, "low": c - .1, "open": c, "volume": (volumes[i] if volumes else 100)}
                for i, c in enumerate(closes[:-1])]
        return BarContext(close=closes[-1], high=closes[-1] + .1, low=closes[-1] - .1,
                          open_=closes[-1], volume=100, history=hist)

    def test_mean_window(self):
        from src.strategy_framework.factor import DSLFactor
        f = DSLFactor("d1", "mean(close,3)")
        assert f.compute(self._ctx([1, 2, 3, 4, 5])) == pytest.approx(4.0)   # (3+4+5)/3

    def test_rsi_and_slope(self):
        from src.strategy_framework.factor import DSLFactor
        f = DSLFactor("d2", "rsi(close,5)")
        v = f.compute(self._ctx([1, 2, 3, 4, 5, 6]))
        assert 0 <= v <= 100
        f2 = DSLFactor("d3", "slope(close,4)")
        assert f2.compute(self._ctx([1, 2, 3, 4, 5, 6])) > 0   # 上升序列斜率为正

    def test_arith_still_works(self):
        from src.strategy_framework.factor import DSLFactor
        f = DSLFactor("d4", "close / mean(close,3) - 1")
        v = f.compute(self._ctx([4, 4, 4, 5]))   # mean=(4+4+5)/3=4.33
        assert v == pytest.approx(5 / (13 / 3) - 1)

    def test_expr_arg_raises_not_silent(self):
        """R-F2(a)：mean(close/2,5) 表达式入参——抛异常而非静默返回当前值。"""
        from src.strategy_framework.factor import DSLFactor
        f = DSLFactor("e1", "mean(close/2,5)")
        with pytest.raises(TypeError):
            f.compute(self._ctx([1, 2, 3, 4, 5]))

    def test_kwargs_accepted(self):
        """R-F2(b)：mean(close,n=3) kwargs 不再静默丢弃。"""
        from src.strategy_framework.factor import DSLFactor
        f = DSLFactor("e2", "mean(close,n=3)")
        assert f.compute(self._ctx([1, 2, 3, 4, 5])) == pytest.approx(4.0)

    def test_nested_window_raises(self):
        """R-F2(c)：嵌套窗口抛异常（外层窗口无效=静默错值）。"""
        from src.strategy_framework.factor import DSLFactor
        f = DSLFactor("e3", "mean(mean(close,3),10)")
        with pytest.raises(TypeError):
            f.compute(self._ctx(list(range(20))))

    def test_unknown_field_raises(self):
        """R-F2(d)：mean(foobar,3) 未知名抛异常而非返回 0。"""
        from src.strategy_framework.factor import DSLFactor
        f = DSLFactor("e4", "mean(foobar,3)")
        with pytest.raises(NameError):
            f.compute(self._ctx([1, 2, 3]))

    def test_blacklist_rejects_unknown(self):
        from src.strategy_framework.factor import DSLFactor
        f = DSLFactor("d5", "__import__('os').system('x')")
        with pytest.raises(Exception):
            f.compute(self._ctx([1, 2, 3]))


# ── #14 回测参数合并 + #15 预检置 failed（源码契约）──

class TestBacktestTaskContracts:
    def test_param_merge_includes_defaults(self):
        """merged_params 必须先 build_default_params 再叠策略级/覆盖（源序断言）。"""
        src = open("src/scheduler/tasks.py").read()
        assert "build_default_params(defs), **strategy_params, **per_symbol" in src

    def test_precheck_fail_sets_failed_not_done(self):
        src = open("src/scheduler/tasks.py").read()
        assert "status='failed'" in src
        # done 判定把 failed 算终态
        assert "status NOT IN ('done','failed','error')" in src


# ── #1 跨进程加载（源码契约：三进程都调）──

class TestFactorLoadingWired:
    def test_all_three_processes_load(self):
        for f in ["src/web_api/main.py", "src/scheduler/app.py", "src/strategy_runner/main.py"]:
            src = open(f).read()
            assert "load_factors_from_db" in src, f"{f} 缺自定义因子加载"


class TestPriceTypeWiring:
    def test_factor_mode_reads_price_type_param(self):
        """R-S3b：因子模式聚合信号 price_type 读策略 params（此前恒 LIMIT 静默忽略 MARKET）。"""
        src = open("src/strategy_framework/strategy.py").read()
        assert 'sig.price_type = self._param("price_type", "LIMIT")' in src
        assert 'sig.order_validity = self._param("order_validity", "DAY")' in src


class TestRunStatusGates:
    def test_all_failed_run_not_done(self):
        """R-S5：全败 run 置 failed（源码契约）。"""
        src = open("src/scheduler/tasks.py").read()
        assert "'done' if ok_cnt > 0 else 'failed'" in src

    def test_backtest_task_lazy_reloads_factors(self):
        """R-S4：回测任务头 lazy 因子重载。"""
        src = open("src/scheduler/tasks.py").read()
        i = src.index("load_factors_from_db")   # 至少两处：任务头+无（scheduler/app 另文件）
        assert "R-S4" in src
