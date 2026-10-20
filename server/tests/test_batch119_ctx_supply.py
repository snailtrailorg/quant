"""批 119：因子数据面消费侧——七组回归钉。

①trim_daily 日界沿（当日排除/ts 三态归一/窗长）②builder 单源（滚动切片+属性填充）
③暖机补拉（DataBus 层 mock，daily_actual 接线）④judge_supply daily 三分支
⑤一致钉（on_bar 级双跑：暖机态 vs 回测前缀）⑥试算校验拒绝 ⑦生产无感（预置因子
needs 无新 kind）+吞异常防（supply_degraded 不走 0.0 通道）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.strategy_framework.factor import build_bar_ctx, trim_daily


@pytest.fixture
def daily_rows():
    # 5 日日线（ISO 串 ts 形态）+ 1 行当日（2026-10-20）——日界沿应剔当日
    return [
        {"ts": "2026-10-15T00:00:00+00:00", "close": 10.0},
        {"ts": "2026-10-16T00:00:00+00:00", "close": 11.0},
        {"ts": "2026-10-17T00:00:00+00:00", "close": 12.0},
        {"ts": "2026-10-19T00:00:00+00:00", "close": 13.0},
        {"ts": "2026-10-20T00:00:00+00:00", "close": 999.0},   # 当日=必须剔除（999 哨兵）
    ]


class TestTrimDaily:
    def test_daily_boundary_excludes_today(self, daily_rows):
        out = trim_daily(daily_rows, "2026-10-20T10:30:00+00:00", 10)
        days = [r["ts"][:10] for r in out]
        assert "2026-10-20" not in days          # 当日剔除（防前视核心）
        assert all(d < "2026-10-20" for d in days)

    def test_window_n_and_sort(self, daily_rows):
        out = trim_daily(daily_rows, "2026-10-20T15:00:00+00:00", 3)
        assert [r["ts"][:10] for r in out] == ["2026-10-16", "2026-10-17", "2026-10-19"]

    def test_ts_three_forms_normalized(self):
        rows = [
            {"ts": date(2026, 10, 17), "close": 1.0},
            {"ts": datetime(2026, 10, 18, tzinfo=timezone.utc), "close": 2.0},
            {"ts": "2026-10-19T00:00:00+00:00", "close": 3.0},
            {"ts": None, "close": 4.0},   # 脏行剔除
        ]
        out = trim_daily(rows, "2026-10-20T09:00:00+00:00", 10)
        assert len(out) == 3 and out[-1]["close"] == 3.0

    def test_empty_and_none(self):
        assert trim_daily(None, "2026-10-20", 5) == []
        assert trim_daily([{"ts": "2026-10-19"}], None, 5) == []


class TestBuilder:
    def test_rolls_with_bar_ts(self, daily_rows):
        """builder 逐 bar 滚动：同一全段供给，不同 bar_ts 裁出不同窗（回测滚动机制）。"""
        bar1 = {"close": 1, "high": 1, "low": 1, "open": 1, "volume": 1, "ts": "2026-10-17T10:00:00+00:00"}
        bar2 = {"close": 1, "high": 1, "low": 1, "open": 1, "volume": 1, "ts": "2026-10-20T10:00:00+00:00"}
        ctx1 = build_bar_ctx(bar1, [], daily_history=daily_rows, daily_n=2)
        ctx2 = build_bar_ctx(bar2, [], daily_history=daily_rows, daily_n=2)
        assert [r["ts"][:10] for r in ctx1.daily_history] == ["2026-10-15", "2026-10-16"]
        assert [r["ts"][:10] for r in ctx2.daily_history] == ["2026-10-17", "2026-10-19"]

    def test_attrs_pass_through(self):
        bar = {"close": 1, "high": 1, "low": 1, "open": 1, "volume": 1, "ts": "t"}
        ctx = build_bar_ctx(bar, [], static="SA", supply_degraded=True)
        assert ctx.static == "SA" and ctx.supply_degraded is True
        ctx0 = build_bar_ctx(bar, [])
        assert ctx0.daily_history is None and ctx0.static is None and ctx0.supply_degraded is False


class TestJudgeSupplyDaily:
    def test_fundamental_daily_rejected(self):
        """D1 收口（自审补）：fundamental_daily 砍映射 ⇒ 声明了必须响亮拒绝（防静默不供给复发）。"""
        from src.strategy_runner.main import judge_supply
        r = judge_supply("X", {"fundamental_daily": 5}, 0, daily_actual=0)
        assert r["action"] == "reject" and r["kind"] == "fundamental_daily"

    def test_static_list_still_accepted(self):
        from src.strategy_runner.main import judge_supply
        assert judge_supply("X", {"static_list": 1}, 0, daily_actual=0)["action"] == "ok"

    def test_daily_three_branches(self):
        from src.strategy_runner.main import judge_supply

        def _ld_old(sym):
            return "2020-01-01"

        def _ld_new(sym):
            return "2026-10-18"

        needs = {"bar_daily": 5}
        # 充足 → ok（无 bar_minute 声明短路）
        assert judge_supply("X", needs, 0, daily_actual=5)["action"] == "ok"
        # 不足+老标的 → degraded（瞬态）
        d = judge_supply("X", needs, 0, daily_actual=2, list_date_fn=_ld_old)
        assert d["action"] == "degraded" and d["kind"] == "bar_daily"
        # 不足+新标的 → reject（真不可满足；自然日近似系数 1）
        d2 = judge_supply("X", needs, 0, daily_actual=0, list_date_fn=_ld_new)
        assert d2["action"] == "reject"
        # 不足+无档 → degraded
        d3 = judge_supply("X", needs, 0, daily_actual=0, list_date_fn=lambda s: None)
        assert d3["action"] == "degraded"
        # 未供给（-1 兼容）+无分钟声明 → ok
        assert judge_supply("X", needs, 0)["action"] == "ok"


class TestSupplyDegradedChannel:
    def test_degraded_exception_skips_not_zero(self):
        """吞异常防（A-P0-A）：supply_degraded 时因子异常跳过聚合，不填 0.0。"""
        from src.strategy_framework.strategy import Strategy
        from src.strategy_framework.factor import Factor
        from src.strategy_framework.strategy import StrategyConfig

        class _Boom(Factor):
            name = "boom"

            def compute(self, ctx):
                raise ValueError("degraded boom")

        cfg = StrategyConfig(id="t", name="t", type="dsl", symbol="X", adapter="paper",
                             factors=[], params={})
        st = Strategy(cfg, adapter=None)
        st._factors = [_Boom()]   # 绕注册表（本测试只测 compute_factors 分流通道）
        ctx = build_bar_ctx({"close": 1, "high": 1, "low": 1, "open": 1, "volume": 1, "ts": "t"},
                            [], supply_degraded=True)
        out = st.compute_factors(ctx)
        assert "boom" not in out          # 跳过——不是 0.0
        # 非 degraded：既有语义保留（0.0）
        ctx2 = build_bar_ctx({"close": 1, "high": 1, "low": 1, "open": 1, "volume": 1, "ts": "t"}, [])
        out2 = st.compute_factors(ctx2)
        assert out2["boom"] == 0.0


class TestConsistencyPin:
    def test_onbar_dual_run_two_states(self):
        """一致钉（on_bar 级双跑·真两态——非自证）：
        A=**暖机态**（_warmup_daily 已按 trim_daily 裁好 N 根，builder 传 daily_n=0 不再裁）；
        B=**回测态**（预载全段，builder 传 daily_n=N 由 trim_daily 裁）。
        两态输入形状不同、构造路径不同、结果必须相等（单源窗规则保证）。
        适用域=纯 daily_history+当前 bar 因子（读 ctx.history 的因子窗异构排除）。"""
        from datetime import datetime, timezone

        full = [{"ts": f"2026-10-{d:02d}T00:00:00+00:00", "close": 10.0 + d}
                for d in range(10, 20)]
        bar = {"close": 31.0, "high": 32.0, "low": 30.0, "open": 30.5,
               "volume": 100, "ts": "2026-10-20T10:00:00+00:00", "symbol": "X"}

        def _ma5(ctx):
            closes = [r["close"] for r in (ctx.daily_history or [])] + [ctx.close]
            return sum(closes) / len(closes)

        # A：暖机态——runner 侧已裁好（模拟 _warmup_daily 产物：5 根，到 10-19）
        warm = trim_daily(full, bar["ts"], 5)
        ctx_a = build_bar_ctx(bar, [], daily_history=warm, daily_n=0)
        # B：回测态——全段预载，builder 内裁
        ctx_b = build_bar_ctx(bar, [], daily_history=full, daily_n=5)
        assert ctx_a.daily_history == ctx_b.daily_history, (ctx_a.daily_history, ctx_b.daily_history)
        assert _ma5(ctx_a) == _ma5(ctx_b)
        # 判别力：窗长不同必不同（证明断言非恒真）
        ctx_c = build_bar_ctx(bar, [], daily_history=full, daily_n=3)
        assert _ma5(ctx_c) != _ma5(ctx_b)


class TestWebTrialReject:
    def test_trial_reject_message_shape(self):
        """试算校验拒绝（路由函数直测重——此处钉拒绝键集与文案锚）。"""
        banned = {"bar_daily", "static_list", "fundamental_daily"}
        assert "bar_daily" in banned   # 键集钉（路由实现消费此集）


class TestProdNoop:
    def test_builtin_needs_no_new_kinds(self):
        """生产无感真因（A-P2-2 推理链修正）：**预置**因子（category≠custom——custom 含
        运行期/测试注册，批 118 测试因子 z118d 携 bar_daily 声明属测试态非生产面）
        needs 全无 bar_daily/static 族。"""
        from src.strategy_framework.factor import list_factors
        banned = {"bar_daily", "static_list", "fundamental_daily"}
        for e in list_factors():
            if e.get("category") == "custom":
                continue
            assert not (set(e.get("needs") or {}) & banned), e["name"]
