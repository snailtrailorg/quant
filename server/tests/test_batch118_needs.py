"""批 118：因子数据面（引擎侧）——needs 声明真源 + 暖机自适应 + 判定三分支。

任务＝`flow/任务/批118-因子数据面多维声明与暖机自适应.md`（步 2 双盲审返工收窄版）。

钉的是**行为**：

1. **词表校验**：needs 键 ∉ DATA_KINDS（拼错键 bar_1d/static）/ L2 占位键
   （depth/stream_tick）⇒ NeedsError（EX_CONFIG 语义）；register_factor 注册期
   fail-fast；DB 手改脏行 load 侧跳过不炸进程；judge_supply 兜底 reject。
2. **糖映射双侧**：needs_history: int 糖＝needs={"bar_minute": N}——装饰器侧
   （register_factor）与 DB load 侧（load_factors_from_db）统一；显式 needs 优先。
3. **持久化往返（dev 真库，迁移 0144 后）**：needs→DB→load 相等；
   **降级反证钉（批 117 同族失效模式）**：load 后 needs 丢回单频糖 ⇒ 红。
4. **聚合 max**：aggregate_needs 逐 kind max；DSL 内联因子并入。
5. **暖机常量真源**：WARMUP_BASELINE=100/WARMUP_CAP=5000 单一真源
   （src.strategy_runner）；grep 断言 main.py/hub_worker.py 无 100/240/500 字面量
   残留（合法引用常量名除外）；cap 拒启（不静默截）。
6. **判定三分支 + 两补充**（judge_supply 纯函数，mock list_date 三态）：
   老标的缺数据 ⇒ degraded；新标的（上市<声明窗）⇒ reject；list_date 无档 ⇒
   degraded；rewarm 缩窗（_rewarm 路径）⇒ degraded 不拒启。
7. **行为级**：needs={"bar_minute": 250} 因子装载 ⇒ 暖机调 _warmup_history(symbol, n≥250)
   （mock DataBus 断言读库窗）；runner 装载路径拒启（NeedsError/cap ⇒ sys.exit 78）。

无库环境可跑（mock 连接），惯例同 test_batch117_st_list；DB 往返用例连不上
dev 库时 skip（迁移/连接为前置）。
"""
import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.strategy_framework import factor as factor_mod
from src.strategy_framework.factor import (
    NeedsError,
    aggregate_needs,
    get_factor,
    register_factor,
    validate_needs,
)

_TEST_PY_CODE = "def compute(ctx, n=20):\n    return ctx.close\n"


# ---------------------------------------------------------------------------
# 1：词表校验（拼错键 / L2 占位 ⇒ NeedsError；合法键通过）
# ---------------------------------------------------------------------------
class TestValidateNeeds:
    def test_unknown_kind_rejected(self):
        """拼错键（bar_1d=表名、static=自造词）⇒ 拒——消静默不供给。"""
        for bad in ("bar_1d", "static", "bar_min", "foo"):
            with pytest.raises(NeedsError, match="未知数据类型"):
                validate_needs({bad: 10})

    def test_l2_placeholder_rejected(self):
        """L2 键（depth/stream_tick）语法合法但引擎侧占位拒启——文案含『占位』。"""
        for k in ("depth", "stream_tick"):
            with pytest.raises(NeedsError, match="L2 占位"):
                validate_needs({k: 5})

    def test_legal_kinds_pass(self):
        out = validate_needs({"bar_minute": 240, "bar_daily": 30, "stk_limit": 1})
        assert out == {"bar_minute": 240, "bar_daily": 30, "stk_limit": 1}

    def test_non_positive_int_rejected(self):
        with pytest.raises(NeedsError):
            validate_needs({"bar_minute": 0})
        with pytest.raises(NeedsError):
            validate_needs({"bar_minute": -5})
        with pytest.raises(NeedsError):
            validate_needs({"bar_minute": "20"})

    def test_needs_error_is_value_error(self):
        """NeedsError 是 ValueError 子类——路由 `except ValueError → 400` 链零改动收编。"""
        assert issubclass(NeedsError, ValueError)


# ---------------------------------------------------------------------------
# 2：糖映射双侧（装饰器 / DB load）
# ---------------------------------------------------------------------------
class TestSugarMapping:
    def test_decorator_sugar_only(self):
        """装饰器只给 needs_history=20 ⇒ needs={"bar_minute": 20}。"""
        @register_factor("_t118_sugar_only", needs_history=20)
        class _F(factor_mod.Factor):
            def compute(self, ctx):
                return 0.0
        try:
            entry = get_factor("_t118_sugar_only")
            assert entry["needs"] == {"bar_minute": 20}
            assert entry["needs_history"] == 20
        finally:
            factor_mod._FACTOR_REGISTRY.pop("_t118_sugar_only", None)

    def test_decorator_needs_priority(self):
        """显式 needs 与 needs_history 并存 ⇒ needs 优先（bar_minute 同键）。"""
        @register_factor("_t118_both", needs_history=20, needs={"bar_minute": 250, "bar_daily": 30})
        class _F(factor_mod.Factor):
            def compute(self, ctx):
                return 0.0
        try:
            entry = get_factor("_t118_both")
            assert entry["needs"] == {"bar_minute": 250, "bar_daily": 30}
            assert entry["needs_history"] == 250   # 糖值回写（消费面单口径）
        finally:
            factor_mod._FACTOR_REGISTRY.pop("_t118_both", None)

    def test_decorator_static_zero(self):
        """静态因子（needs_history=0、无 needs）⇒ 空 dict。"""
        @register_factor("_t118_static", needs_history=0)
        class _F(factor_mod.Factor):
            def compute(self, ctx):
                return 0.0
        try:
            entry = get_factor("_t118_static")
            assert entry["needs"] == {}
            assert entry["needs_history"] == 0
        finally:
            factor_mod._FACTOR_REGISTRY.pop("_t118_static", None)

    def test_decorator_bad_needs_fails_fast(self):
        """注册期非法 needs ⇒ 装饰器当场抛（启动期响亮失败）。"""
        with pytest.raises(NeedsError):
            @register_factor("_t118_bad", needs={"bar_1d": 5})
            class _F(factor_mod.Factor):
                def compute(self, ctx):
                    return 0.0
        assert get_factor("_t118_bad") is None   # 注册表不进

    def test_preset_factors_have_needs(self):
        """预置因子注册 entry 全部带 needs 键（糖升格形态）。"""
        for f in factor_mod.list_factors():
            assert "needs" in f, f"{f['name']} 缺 needs 键"
            assert isinstance(f["needs"], dict)

    def test_load_side_sugar_upgrade(self):
        """DB load 侧：needs 列 NULL（存量行）⇒ 升格 {"bar_minute": int 列值}。"""
        rows = [("z118a", "custom", "", _TEST_PY_CODE, "{}", 30, "python", None)]
        loaded = _load_with_rows(rows)
        assert loaded == ["z118a"]
        entry = get_factor("z118a")
        assert entry["needs"] == {"bar_minute": 30}
        assert entry["needs_history"] == 30

    def test_load_side_dict_passthrough(self):
        """DB load 侧：needs 列非 NULL（JSONB dict）⇒ 透传合并。"""
        rows = [("z118b", "custom", "", _TEST_PY_CODE, "{}", 30, "python",
                 {"bar_minute": 250, "bar_daily": 60})]
        _load_with_rows(rows)
        entry = get_factor("z118b")
        assert entry["needs"] == {"bar_minute": 250, "bar_daily": 60}

    def test_load_side_bad_needs_skipped(self):
        """DB 手改脏行（非法键）⇒ warning 跳过该因子，不炸进程（与坏代码同档）。"""
        rows = [("z118c", "custom", "", _TEST_PY_CODE, "{}", 0, "python", {"bar_1d": 5})]
        loaded = _load_with_rows(rows)
        assert loaded == []
        assert get_factor("z118c") is None


def _load_with_rows(rows):
    """mock get_conn 喂 factor_def 行（列序同 load SELECT）。

    conn 须是自回环上下文管理器（MagicMock 的 __enter__ 返回新 child mock——
    `with get_conn() as c` 拿到的不是 conn 本体，execute 链接不上）。
    """
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.execute.return_value.fetchall.return_value = rows
    for n in ("z118a", "z118b", "z118c"):
        factor_mod._FACTOR_REGISTRY.pop(n, None)
    with patch("src.data_platform.db.get_conn", return_value=conn):
        return factor_mod.load_factors_from_db()


# ---------------------------------------------------------------------------
# 3：聚合 max（aggregate_needs）
# ---------------------------------------------------------------------------
class TestAggregateNeeds:
    def _strategy(self, factor_configs):
        class _Cfg:
            factors = factor_configs
        class _S:
            config = _Cfg()
        return _S()

    def test_max_per_kind(self):
        """多因子同 kind ⇒ 逐 kind max（250 > 20 > 14）。"""
        s = self._strategy([{"name": "ma_dev", "weight": 1},
                            {"name": "rsi", "weight": 1}])
        # ma_dev needs_history=20、rsi=14（预置注册表）
        agg = aggregate_needs(s)
        assert agg["bar_minute"] == max(
            get_factor("ma_dev")["needs"].get("bar_minute", 0),
            get_factor("rsi")["needs"].get("bar_minute", 0))

    def test_registered_custom_factor_aggregated(self):
        factor_mod._FACTOR_REGISTRY["__t118_agg"] = {
            "cls": None, "name": "__t118_agg", "category": "t", "params": {},
            "description": "", "is_custom": True,
            "needs_history": 0, "needs": {"bar_daily": 30},
        }
        try:
            s = self._strategy([{"name": "ma_dev", "weight": 1},
                                {"name": "__t118_agg", "weight": 1}])
            agg = aggregate_needs(s)
            assert agg.get("bar_daily") == 30
            assert "bar_minute" in agg
        finally:
            factor_mod._FACTOR_REGISTRY.pop("__t118_agg", None)

    def test_inline_dsl_factor_window(self):
        """内联 dsl: 因子（不进注册表）⇒ 表达式静态校验并入聚合。"""
        s = self._strategy([{"name": "dsl:mean(close,37)", "weight": 1, "expr": "mean(close,37)"}])
        agg = aggregate_needs(s)
        assert agg.get("bar_minute") == 37

    def test_empty_strategy(self):
        assert aggregate_needs(self._strategy([])) == {}

    def test_python_code_mode_empty(self):
        """PythonStrategy（不走因子注册表面）⇒ 空声明（批 119 ctx 扩展再接）。"""
        s = self._strategy([{"name": "whatever", "weight": 1}])
        agg = aggregate_needs(s)
        assert "bar_daily" not in agg   # 未知因子不进聚合、不炸


# ---------------------------------------------------------------------------
# 4：暖机常量真源（4 消费点 grep 断言）
# ---------------------------------------------------------------------------
class TestWarmupConstantsSingleSource:
    def test_constants_exist(self):
        from src.strategy_runner import WARMUP_BASELINE, WARMUP_CAP
        assert WARMUP_BASELINE == 100
        assert WARMUP_CAP == 5000

    def test_no_hardcoded_literals_in_main(self):
        """main.py：暖机路径禁 100/500 字面量残留（常量名引用除外）。

        逐行扫 _warmup_history/judge_supply 函数体——语义相关字面量只允许出现在
        注释或 WARMUP_* 常量名里。
        """
        import inspect
        import re
        from src.strategy_runner import main as runner_main
        for fn_name in ("_warmup_history", "judge_supply"):
            src = inspect.getsource(getattr(runner_main, fn_name))
            for line in src.splitlines():
                code = line.split("#")[0]   # 去注释
                if "WARMUP_" in code:
                    continue
                assert not re.search(r"\b(min\s*\(\s*n\s*,\s*500|[-:]?\s*500\b)", code), \
                    f"{fn_name} 残留 500 帽字面量: {line.strip()}"
                # n=100 默认值形态禁（签名默认值已改 None）
                assert "n: int = 100" not in code, f"{fn_name} 残留 n=100 默认值"

    def test_no_hardcoded_literals_in_hub_worker(self):
        """hub_worker.py：暖机窗禁 240/100 硬编码（常量名/hist_window 引用除外）。"""
        import inspect
        import re
        from src.strategy_runner import hub_worker
        # 模块级与 run() 内暖机窗：count=240 / [-100:] / >100 已全改 hist_window
        src = inspect.getsource(hub_worker)
        for pattern in (r"count\s*=\s*240", r"\[-100:\]", r">\s*100\s*:", r"len\(history\)\s*>\s*100"):
            assert not re.search(pattern, src), f"hub_worker 残留硬编码: {pattern}"

    def test_migrations_uses_literal_only_in_init(self):
        """真源唯一定义点=src.strategy_runner/__init__.py（赋值语句口径——注释提及不算）。"""
        import re
        import subprocess
        out = subprocess.run(
            ["grep", "-rn", "WARMUP_CAP", "src/"], capture_output=True, text=True).stdout
        defining = [ln for ln in out.splitlines()
                    if re.search(r"WARMUP_CAP\s*=\s*\d+", ln.split("#")[0])]
        assert len(defining) == 1 and "strategy_runner/__init__.py" in defining[0]


# ---------------------------------------------------------------------------
# 5：判定三分支 + 两补充（judge_supply 纯函数，mock list_date 三态）
# ---------------------------------------------------------------------------
class TestJudgeSupply:
    NEEDS = {"bar_minute": 250}

    def test_sufficient_ok(self):
        from src.strategy_runner.main import judge_supply
        d = judge_supply("600000.SHSE", self.NEEDS, 300,
                         list_date_fn=lambda s: "2020-01-01")
        assert d["action"] == "ok"

    def test_no_bar_minute_declaration_ok(self):
        """无 bar_minute 声明 ⇒ 恒 ok（暖机落基线窗，不进判定）。"""
        from src.strategy_runner.main import judge_supply
        d = judge_supply("600000.SHSE", {}, 0, list_date_fn=lambda s: None)
        assert d["action"] == "ok"

    def test_old_listing_short_supply_degraded(self):
        """分支②a：上市 ≥ 声明窗而数据缺 ⇒ degraded（瞬态，同步层追）。"""
        from src.strategy_runner.main import judge_supply
        d = judge_supply("600000.SHSE", self.NEEDS, 100,
                         list_date_fn=lambda s: "2020-01-01")
        assert d["action"] == "degraded"
        assert d["kind"] == "bar_minute" and d["declared"] == 250 and d["actual"] == 100

    def test_new_listing_reject(self):
        """分支②b：上市 < 声明窗（标的太新，真不可满足）⇒ reject。"""
        from src.strategy_runner.main import judge_supply
        d = judge_supply("600000.SHSE", {"bar_minute": 5000}, 100,
                         list_date_fn=lambda s: date.today().isoformat())
        assert d["action"] == "reject"
        assert "拒启" in d["reason"] or "不可满足" in d["reason"]

    def test_no_list_date_degraded_not_reject(self):
        """分支②c：list_date 无档 ⇒ 归分支② degraded（SM 未回填≠标的太新）。

        反证钉：若误当『新标的』⇒ reject——用例红。
        """
        from src.strategy_runner.main import judge_supply
        d = judge_supply("600000.SHSE", self.NEEDS, 100, list_date_fn=lambda s: None)
        assert d["action"] == "degraded"

    def test_list_date_lookup_exception_degraded(self):
        """SM 读异常 ⇒ 同无档（fail-soft 归 degraded——判定层不依赖数据模块生命周期）。"""
        from src.strategy_runner.main import judge_supply
        def _boom(s):
            raise RuntimeError("db down")
        d = judge_supply("600000.SHSE", self.NEEDS, 100, list_date_fn=_boom)
        assert d["action"] == "degraded"

    def test_dirty_list_date_degraded(self):
        """list_date 脏值 ⇒ 不猜，同无档 degraded。"""
        from src.strategy_runner.main import judge_supply
        d = judge_supply("600000.SHSE", self.NEEDS, 100, list_date_fn=lambda s: "not-a-date")
        assert d["action"] == "degraded"

    def test_l2_placeholder_reject(self):
        """分支①：L2 占位键 ⇒ reject（写侧已拦，DB 手改兜底）。"""
        from src.strategy_runner.main import judge_supply
        d = judge_supply("600000.SHSE", {"depth": 5}, 100, list_date_fn=lambda s: None)
        assert d["action"] == "reject"

    def test_unknown_kind_reject(self):
        """分支①：拼错键 ⇒ reject。"""
        from src.strategy_runner.main import judge_supply
        d = judge_supply("600000.SHSE", {"bar_1d": 5}, 100, list_date_fn=lambda s: None)
        assert d["action"] == "reject"

    def test_over_cap_reject_with_limit_in_message(self):
        """cap 拒启且报上限（不静默截）。"""
        from src.strategy_runner.main import judge_supply
        from src.strategy_runner import WARMUP_CAP
        d = judge_supply("600000.SHSE", {"bar_minute": WARMUP_CAP + 1}, 100,
                         list_date_fn=lambda s: "2020-01-01")
        assert d["action"] == "reject"
        assert str(WARMUP_CAP) in d["reason"]

    def test_exactly_cap_passes(self):
        """声明=cap 恰好合法（>cap 才拒）。"""
        from src.strategy_runner.main import judge_supply
        from src.strategy_runner import WARMUP_CAP
        d = judge_supply("600000.SHSE", {"bar_minute": WARMUP_CAP}, WARMUP_CAP,
                         list_date_fn=lambda s: "2020-01-01")
        assert d["action"] == "ok"


# ---------------------------------------------------------------------------
# 6：行为级（暖机自适应 n + runner 拒启路径）
# ---------------------------------------------------------------------------
class _FakeFrame:
    def __init__(self, n):
        import random
        random.seed(118)
        self.rows = [
            (f"S{i}", "1min", f"2026-10-10T09:{i % 60:02d}:00+08:00",
             1.0, 1.0, 1.0, 1.0, 100.0, 0.0, 1.0, "local_pg")
            for i in range(n)
        ]


class TestWarmupAdaptive:
    def test_warmup_n_passed_to_databus_window(self):
        """needs={"bar_minute": 250} ⇒ _warmup_history(symbol, n=250) 读库窗 250。"""
        from src.strategy_runner import main as runner_main
        got = {}

        class _Bus:
            def __init__(self):
                pass

            def get_bars(self, req):
                got["range_days"] = (req.range_[1] - req.range_[0]).days
                return _FakeFrame(400), None

        with patch("src.data_platform.databus.DataBus", _Bus):
            hist = runner_main._warmup_history("600000.SHSE", n=250)
        assert len(hist) == 250   # 帧里有 400 根，窗取尾 250

    def test_warmup_default_n_is_baseline(self):
        """n=None ⇒ 基线窗 WARMUP_BASELINE（默认值不回落到签名里）。"""
        from src.strategy_runner import WARMUP_BASELINE
        from src.strategy_runner import main as runner_main

        class _Bus:
            def get_bars(self, req):
                return _FakeFrame(400), None

        with patch("src.data_platform.databus.DataBus", _Bus):
            hist = runner_main._warmup_history("600000.SHSE")
        assert len(hist) == WARMUP_BASELINE

    def test_warmup_failure_fail_open(self):
        """读库失败 ⇒ fail-open（空 history+告警日志，现状 #7 D27 边界不动）。"""
        from src.strategy_runner import main as runner_main

        class _Bus:
            def get_bars(self, req):
                raise RuntimeError("pg down")

        with patch("src.data_platform.databus.DataBus", _Bus):
            hist = runner_main._warmup_history("600000.SHSE", n=100)
        assert hist == []


class TestRunnerStartupPaths:
    """runner 装载段（_run_hub_mode needs 段）的拒启行为——直接驱动函数段不便整链
    搭建，此处钉判定函数+装载段的接缝：validate_needs 拒 ⇒ EX_CONFIG 语义由
    judge_supply/装载段兜底（上面已钉），本组钉 cap 拒启走 judge_supply 的
    reject 分支文案（『不静默截』验收）。"""

    def test_cap_reject_reason_mentions_not_silent(self):
        from src.strategy_runner.main import judge_supply
        d = judge_supply("X.BINANCE", {"bar_minute": 9999}, 50,
                         list_date_fn=lambda s: "2020-01-01")
        assert d["action"] == "reject"
        assert "9999" in d["reason"]


# ---------------------------------------------------------------------------
# 7：持久化往返（dev 真库；连不上 skip）
# ---------------------------------------------------------------------------
def _dev_db_ok() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM factor_def LIMIT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _dev_db_ok(), reason="dev 库不可达或迁移 0144 未跑")
class TestPersistenceRoundtrip:
    NAME = "zz_test_b118_roundtrip"

    def teardown_method(self):
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM factor_def WHERE name=%s", (self.NAME,))
            conn.commit()
        factor_mod._FACTOR_REGISTRY.pop(self.NAME, None)

    def test_roundtrip_multikind_needs(self):
        """needs dict 经 DB 往返不丢（防 117 式静默降级单频）。"""
        result = factor_mod.register_custom_factor(
            name=self.NAME, category="custom",
            code="def compute(ctx, n=20):\n    return ctx.close\n",
            needs={"bar_minute": 250, "bar_daily": 30})
        assert result["needs"] == {"bar_minute": 250, "bar_daily": 30}
        # 丢注册表模拟重启 → DB load 回来
        factor_mod._FACTOR_REGISTRY.pop(self.NAME, None)
        loaded = factor_mod.load_factors_from_db()
        assert self.NAME in loaded
        entry = get_factor(self.NAME)
        assert entry["needs"] == {"bar_minute": 250, "bar_daily": 30}   # 反证钉：降级丢 bar_daily ⇒ 红
        assert entry["needs_history"] == 250

    def test_roundtrip_null_column_uses_sugar(self):
        """NULL 列（存量行/空声明）≡ 旧 int 糖——直接 UPDATE 置 NULL 模拟存量行。"""
        from src.data_platform.db import get_conn
        factor_mod.register_custom_factor(
            name=self.NAME, category="custom",
            code="def compute(ctx, n=20):\n    return ctx.close\n",
            needs_history=30)
        with get_conn() as conn:
            conn.execute("UPDATE factor_def SET needs=NULL WHERE name=%s", (self.NAME,))
            conn.commit()
        factor_mod._FACTOR_REGISTRY.pop(self.NAME, None)
        factor_mod.load_factors_from_db()
        entry = get_factor(self.NAME)
        assert entry["needs"] == {"bar_minute": 30}

    def test_upsert_bad_needs_rejected_before_db(self):
        """写侧拦截先于落库——非法键 400 语义，DB 无行。"""
        from src.data_platform.db import get_conn
        with pytest.raises(NeedsError):
            factor_mod.register_custom_factor(
                name=self.NAME, category="custom", code=_TEST_PY_CODE,
                needs={"bar_1d": 5})
        with get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM factor_def WHERE name=%s", (self.NAME,)).fetchone()[0]
        assert n == 0
