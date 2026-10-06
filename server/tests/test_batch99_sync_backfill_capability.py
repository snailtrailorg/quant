"""批 99：数据同步能力接线钉（能力真源 / 契约接线 / 逐日循环接日历）。

三组，各钉一类真故障：

1. **能力真源双向**（`TestBackfillCapabilityTruth`）——`sync_config.supports_backfill` 必须
   **恰等于**代码结构真源 `_VIA_KIND_IDS ∪ _TIER1_BATCH ∪ {astock_basic, trade_cal}`。
   钉的是「UI 回补门的判据」：旧实现用 `sync_mode`（形态标签）当门，两个方向都错
   （`trade_cal` 该有没有、`pool_data` 不该有却有）。**反证**：把 DB 任一行的
   supports_backfill 改错、或把某项从 `_TIER1_BATCH` 移出，本组必红。
2. **契约层接线**（`TestContractWiring`）——adapter 的 `capability_decls` **真声明**（非空且
   合法），`register_adapter` 钩子对非法声明真抛；`sync_kind_config.kind` 全被某 adapter
   声明覆盖；`SYNC_ID_CAP_MAP` ⊇ 全部 sync_id（补 4 缺项后达标）。
3. **逐日循环接交易日历**（`TestTradeDayLoop`）——`_sync_by_trade_date`（A股基本面）与
   `_sync_via_kind_daily_batch`（bar 族日线）都走 `_trade_dates_in_range`；
   日历未覆盖时 fail-open 回 `freq="B"`。**反证**：回退实现即红。

DB 依赖：真库直查（无 dev 库自动跳过，惯例同 test_account_permission._db_up）。
"""
import datetime as _dt
import os
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 真源字面量：真吃 backfill_from 的 20 项（与迁移 0131 的 seed 同源；批 101 加加密 1 项；
# 批 103b 加聚宽 A 股日线 1 项；批 101b 加加密盘中 bar 3 项）
BACKFILLABLE_EXPECTED = {
    # bar 族 6（_VIA_KIND_IDS）
    "astock_daily", "etf_daily", "cb_daily", "index_daily",
    "astock_minute", "astock_minute_5min",
    # tier1 批量 7（_TIER1_BATCH）
    "stk_limit_sync", "moneyflow_sync", "margin_detail_sync", "top_list_sync",
    "block_trade_sync", "cyq_perf_sync", "forecast_sync",
    # 单表专项 8（astock_basic / trade_cal / 批 101 binance_perp_daily / 批 103b astock_daily_jq
    #   / 批 101b binance_perp_{hourly,1min,15min}）
    "astock_basic", "trade_cal", "binance_perp_daily", "astock_daily_jq",
    "binance_perp_hourly", "binance_perp_1min", "binance_perp_15min",
}
# 必须**没有**回补入口的 9 项（静态清单 5 / 全量重建 2 / 池内 2）
NOT_BACKFILLABLE_EXPECTED = {
    "astock_list", "cb_basic", "etf_list", "static_symbols", "convertible_terms",
    "namechange_sync", "concept_sync", "pool_data", "pool_data_full_calibrate",
}


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _q(sql: str) -> list:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        return conn.execute(sql).fetchall()


class TestBackfillCapabilityTruth:
    """DB 投影 ↔ 代码结构真源（双向）。"""

    def test_code_truth_equals_literal(self):
        """代码结构推出的可回补集 == 字面量 20 项（防「加/删 handler 忘更新声明」）。"""
        from src.data_sync.engine import _HANDLERS, _TIER1_BATCH, _TIER1_FULL, _VIA_KIND_IDS
        # 单表专项＝「不在两个工厂里、但 handler 真读 backfill_from」的项（binance_perp_* /
        # astock_daily_jq 经 _HANDLERS 直挂，与 astock_basic/trade_cal 同族）
        code_truth = (set(_VIA_KIND_IDS) | set(_TIER1_BATCH)
                      | {"astock_basic", "trade_cal", "binance_perp_daily", "astock_daily_jq",
                         "binance_perp_hourly", "binance_perp_1min", "binance_perp_15min"})
        assert code_truth == BACKFILLABLE_EXPECTED, (
            f"代码真源漂移：多 {sorted(code_truth - BACKFILLABLE_EXPECTED)} / "
            f"少 {sorted(BACKFILLABLE_EXPECTED - code_truth)}")
        # 两个全量重建工厂产出的 handler 不消费 backfill_from（唯一一处「收了参数但忽略」的形态）
        assert not (set(_TIER1_FULL) & BACKFILLABLE_EXPECTED)
        # 全部可回补项必须都有 handler（防空声明指向不存在的实现）
        assert BACKFILLABLE_EXPECTED <= (set(_HANDLERS) | set(_VIA_KIND_IDS))

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_db_flag_matches_code_truth(self):
        db_true = {r[0] for r in _q("SELECT id, supports_backfill FROM sync_config") if r[1]}
        assert db_true == BACKFILLABLE_EXPECTED, (
            f"DB supports_backfill 与代码真源不符：多 {sorted(db_true - BACKFILLABLE_EXPECTED)} / "
            f"少 {sorted(BACKFILLABLE_EXPECTED - db_true)}")

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_false_set_covers_rest(self):
        """其余 9 项必须显式 false（含 pool_data——旧 UI 的假按钮就是它）。"""
        db_false = {r[0] for r in _q("SELECT id, supports_backfill FROM sync_config") if not r[1]}
        assert db_false == NOT_BACKFILLABLE_EXPECTED
        assert "pool_data" in db_false and "trade_cal" not in db_false

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_start_floor_seeded_only_where_known(self):
        """start_floor 只填已实证的下界，其余 NULL（不臆造）。

        批 101 新增 `binance_perp_daily='2019-09-08'`——USDT-M 永续上线日（币安官方事实），
        且 adapter 的 pull_daily 真能投递该起点（早期靠日包；月包自 2020-01）。
        批 101b 新增三条**存储闸门**下界（迁移 0134，`CURRENT_DATE-N` 相对量）：
        hourly＝执行日前推 7 天、1min/15min＝前推 1 天——「功能验证档」的有界占用上限，
        不是上游能力边界（上游有全史，是磁盘没到）。故此处只钉「非 NULL」，值随迁移执行日变。
        """
        rows = dict(_q("SELECT id, start_floor FROM sync_config"))
        assert str(rows["astock_basic"]) == "1990-12-19"
        assert str(rows["index_daily"]) == "2005-04-08"
        assert str(rows["binance_perp_daily"]) == "2019-09-08"
        assert {k for k, v in rows.items() if v is not None} == {
            "astock_basic", "index_daily", "binance_perp_daily",
            "binance_perp_hourly", "binance_perp_1min", "binance_perp_15min"}
        assert rows["binance_perp_hourly"] < rows["binance_perp_1min"], \
            "hourly 的保留窗更长（7 天 vs 1 天）"

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_columns_shape(self):
        """迁移 0131 的两列形态（supports_backfill NOT NULL 默认 false；start_floor 可空）。"""
        rows = {r[0]: r for r in _q(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name='sync_config' "
            "AND column_name IN ('supports_backfill','start_floor')")}
        assert rows["supports_backfill"][1] == "boolean"
        assert rows["supports_backfill"][2] == "NO"
        assert "false" in (rows["supports_backfill"][3] or "")
        assert rows["start_floor"][1] == "date"
        assert rows["start_floor"][2] == "YES"


class TestContractWiring:
    """契约层：真声明 + 钩子执法 + 词表完备。"""

    def test_tushare_declares_capability_decls(self):
        from src.data_platform.adapters.base import TushareAdapter
        from src.quant_common.contract import validate_capability_decls
        decls = TushareAdapter.capability_decls
        assert decls, "批 99 起 adapter 必须有真声明（否则 register_adapter 钩子空转）"
        assert validate_capability_decls(decls) == []

    def test_hook_bites_on_illegal_decl(self):
        """反证：非法声明（非聚合 kind 带 sub_kinds）经 register_adapter 必须 ValueError。"""
        from src.data_platform.adapters import base as base_mod
        from src.quant_common.contract import ASTOCK_ALL, CapabilityDecl
        try:
            @base_mod.register_adapter
            class _Bad99:
                provider = "__bad99__"
                capability_decls = [CapabilityDecl("bar_daily", "historical", ASTOCK_ALL,
                                                   sub_kinds=frozenset({"x"}))]
            raise AssertionError("非法 capability_decls 未被装饰器拦截")
        except ValueError as e:
            assert "capability" in str(e).lower()
        finally:
            base_mod._ADAPTERS.pop("__bad99__", None)

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_all_kinds_declared_by_some_adapter(self):
        """`sync_kind_config.kind` 必须全被某 adapter 声明（否则该族无源可拉）。"""
        from src.data_platform.adapters.base import _ADAPTERS
        declared = {d.kind for c in _ADAPTERS.values()
                    for d in getattr(c, "capability_decls", [])}
        kinds = {r[0] for r in _q("SELECT DISTINCT kind FROM sync_kind_config")}
        assert kinds <= declared, f"未被任何 adapter 声明的 kind：{sorted(kinds - declared)}"

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_cap_map_covers_every_sync_id(self):
        """SYNC_ID_CAP_MAP ⊇ sync_config.id ∪ sync_kind_config.sync_id（批 99 补 4 缺项后达标）。

        改成「完备性」而非字面量键数：加同步项忘了登记即红，而不用跟着改数字。
        """
        from src.quant_common.markets import CAPABILITIES, SYNC_ID_CAP_MAP
        ids = {r[0] for r in _q("SELECT id FROM sync_config")}
        ids |= {r[0] for r in _q("SELECT sync_id FROM sync_kind_config")}
        assert ids <= set(SYNC_ID_CAP_MAP), (
            f"能力映射表缺登记（清单有·能力无）：{sorted(ids - set(SYNC_ID_CAP_MAP))}")
        assert set(SYNC_ID_CAP_MAP.values()) <= set(CAPABILITIES)

    @pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
    def test_tushare_capabilities_cover_all_its_sync_ids(self):
        """tushare 的 capabilities 必须覆盖它名下全部 sync_config 行（4 缺项已补）。"""
        from src.data_platform.adapters.base import TushareAdapter
        own = {r[0] for r in _q("SELECT id FROM sync_config WHERE provider='tushare'")}
        assert own <= set(TushareAdapter.capabilities), (
            f"tushare 能力矩阵漏声明：{sorted(own - set(TushareAdapter.capabilities))}")


class TestConfigEndpoint:
    """`/api/sync/config` 必须带出能力两键（前端门的输入）。"""

    def _row(self, supports, floor):
        return ("trade_cal", "交易日历", "pro.trade_cal", "trade_cal", "astock", "full",
                "0 9 1 * *", "none", True, None, None, 0, "idle", "", "tushare",
                supports, floor)

    def _call(self, row):
        from src.web_api.routes import sync as sync_routes

        class _C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, args=()):
                assert "supports_backfill" in sql and "start_floor" in sql, \
                    "路由 SQL 必须显式取两新列（该端点用列清单、非 SELECT *）"
                return SimpleNamespace(fetchall=lambda: [row])

        with patch.object(sync_routes, "get_conn", return_value=_C()):
            return sync_routes.list_sync_config()

    def test_serializes_capability_and_floor(self):
        out = self._call(self._row(True, _dt.date(2005, 4, 8)))
        assert out[0]["supports_backfill"] is True
        assert out[0]["start_floor"] == "20050408"

    def test_null_floor_is_none(self):
        out = self._call(self._row(False, None))
        assert out[0]["supports_backfill"] is False
        assert out[0]["start_floor"] is None


class TestTradeDayLoop:
    """两个逐日循环都接交易日历（批 99 合并待办 98——原为两处 freq="B" 漏网）。"""

    def _patch_common(self):
        return [
            patch.object(__import__("src.data_sync.engine", fromlist=["x"]), "_get_rate_ds",
                         return_value=object()),
            patch("src.data_platform.rate_limit.rate_limit_context",
                  lambda *a, **k: nullcontext()),
        ]

    def test_sync_by_trade_date_uses_calendar(self):
        from src.data_sync import engine
        seen = []
        with patch.object(engine, "_trade_dates_in_range",
                          return_value=["20260105", "20260106"]), \
             patch.object(engine, "_expected_trading_days", return_value=2), \
             self._patch_common()[0], self._patch_common()[1]:
            r = engine._sync_by_trade_date(lambda trade_date: seen.append(trade_date),
                                           lambda df: 0, "20260101", "20260110")
        assert seen == ["20260105", "20260106"]
        assert r["expected_days"] == 2 and not r["failed_dates"]

    def test_sync_by_trade_date_fail_open(self):
        from src.data_sync import engine
        seen = []
        with patch.object(engine, "_trade_dates_in_range", return_value=None), \
             patch.object(engine, "_expected_trading_days", return_value=5), \
             self._patch_common()[0], self._patch_common()[1]:
            engine._sync_by_trade_date(lambda trade_date: seen.append(trade_date),
                                       lambda df: 0, "20260105", "20260109")
        # 2026-01-05(一) ~ 01-09(五) 全工作日，fail-open 回 freq="B"
        assert seen == ["20260105", "20260106", "20260107", "20260108", "20260109"]

    def test_via_kind_daily_batch_uses_calendar(self):
        from src.data_sync import engine
        seen = []

        def fake_fetch(adapter, *, kind, sub_kind, symbols, start, end, freq):
            seen.append(start)
            return SimpleNamespace(rows=())

        with patch.object(engine, "_trade_dates_in_range", return_value=["20260105"]), \
             patch.object(engine, "_api_name_of", return_value="daily"), \
             patch.object(engine, "_fetch_supply", side_effect=fake_fetch), \
             self._patch_common()[0], self._patch_common()[1]:
            engine._sync_via_kind_daily_batch(SimpleNamespace(provider="tushare"), sub="stock",
                                              cfg={"id": "astock_daily"},
                                              start="20260101", end_date="20260110")
        assert seen == ["20260105"]
