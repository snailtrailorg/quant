"""批 109：per-date 分族对账 —— 日期级差集 → 内联重拉 → 视图式落表（设计 §7.1/§7.2）。

钉的是**行为**（不是「函数存在」）：

1. **期望集唯一来源** `_trade_dates_in_range`（覆盖感知）；`None` ⇒ 对账侧 **fail-closed 到
   `uncertain`**——**不主张缺口、不重拉**。这是步 2 双盲审 P0-2 的落点：拉取侧可 fail-open 回
   `freq="B"`，把同一个 `None` 拿到对账侧就会**把节假日当期望日产出假缺口**。
2. **本地不可判 ⇒ uncertain**（`_local_dates` 返回 `None`，不是空集）——空集会产全窗口假缺口。
3. **内联重拉后复查**：补齐的锚**不得**留 open；仍缺 ⇒ 落 `open` ＋ 聚合告警（`sync()` 侧）。
4. **预算耗尽截断**：只重拉余量内的缺日，其余**照实落 open**（不静默丢、不虚报补上）。
5. **四处站点真接线**（P1 接线面）：`_sync_via_kind_daily_batch` / `_sync_via_kind_cb_daily` /
   `_sync_astock_daily_jq` / `_make_tier1_handler` 的返回体都带 `gap_dates`；A 股四站点把
   `_family_start` 的第二返回值（`excluded`）透传出去。
6. **转债宇宙取 SM**（裁定 Q3）：cb 的 `where` 走 `security_master.category='convertible'`，
   **不是** symbol 前缀（前缀法实测漏 18 只）。

DB 依赖：`sync_gap` 落表断言走真库（无库自动跳过）；读取口/拉取口全 mock（不连 Tushare/JQ）。
测试只写 `__t109r` 命名空间且用完即清。
"""
from contextlib import nullcontext
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data_sync import engine

NS = "__t109r"


def _nullctx(*_a, **_k):
    """`rate_limit_context` 替身：限速治理不是本批的被测面（真源在 `rate_limit` 单测里钉）。"""
    return nullcontext()


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


def _clean() -> None:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM sync_gap WHERE LEFT(sync_id, %s) = %s", (len(NS), NS))
        conn.commit()


def _rows(sid: str) -> list[tuple]:
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rs = conn.execute(
            "SELECT symbol, gap_start, gap_end, state FROM sync_gap WHERE sync_id=%s "
            "ORDER BY gap_start", (sid,)).fetchall()
    return [(r[0], str(r[1]), str(r[2]), r[3]) for r in rs]


@pytest.fixture(autouse=True)
def _clean_ns():
    try:
        _clean()
    except Exception:  # noqa: S110
        pass
    yield
    try:
        _clean()
    except Exception:  # noqa: S110
        pass


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """本模块所有站点调用都不进限速治理（与拉取口径无关，见 `_nullctx`）。"""
    with patch("src.data_platform.rate_limit.rate_limit_context", _nullctx):
        yield


# ---------------------------------------------------------------------------
# 1：缺日 → 连续段（按期望集顺序合并）
# ---------------------------------------------------------------------------


class TestSegments:
    def test_consecutive_expected_merge_into_one_span(self):
        exp = ["20260105", "20260106", "20260107", "20260108"]
        assert engine._to_segments(["20260105", "20260106"], exp) == [("20260105", "20260106")]

    def test_hole_splits_spans(self):
        exp = ["20260105", "20260106", "20260107", "20260108"]
        assert engine._to_segments(["20260105", "20260108"], exp) == [
            ("20260105", "20260105"), ("20260108", "20260108")]

    def test_non_trading_gap_does_not_split(self):
        """期望集**相邻**即同段——周末/节假日不在期望集里，不得把跨周末的缺口切成两段。"""
        exp = ["20260108", "20260109", "20260112", "20260113"]     # 中间跳过周末
        assert engine._to_segments(["20260109", "20260112"], exp) == [("20260109", "20260112")]

    def test_empty(self):
        assert engine._to_segments([], ["20260105"]) == []
        assert engine._to_segments(["20260105"], []) == []


# ---------------------------------------------------------------------------
# 2：差集 → 重拉 → 落表（含 uncertain 两道门与预算截断）
# ---------------------------------------------------------------------------


class TestReconcileDates:
    def _call(self, sid, expected, local_seq, repull=None, max_repull=None, table="bar_1d"):
        """`_local_dates` 按序返回 local_seq 的元素（末项重复），其余全 mock。"""
        seq = list(local_seq)

        def _fake_local(table_, dexpr, where="", params=()):
            return seq.pop(0) if len(seq) > 1 else seq[0]

        with patch.object(engine, "_local_dates", side_effect=_fake_local):
            return engine._reconcile_dates(
                sid, table=table, date_expr="X", expected=expected,
                win_lo="20260101", win_hi="20260131", repull_fn=repull, max_repull=max_repull)

    @needs_db
    def test_repull_fills_and_no_open_row(self):
        sid, exp = NS + "_a", ["20260105", "20260106", "20260107"]
        calls: list[str] = []

        def _repull(day):
            calls.append(day)

        rec = self._call(sid, exp, [{"20260105"}, exp], repull=_repull)
        assert calls == ["20260106", "20260107"], "逐缺日重拉"
        assert rec["missing"] == 2 and rec["gap_dates"] == []
        assert rec["new_segments"] == [], "补齐 ⇒ 无新 open ⇒ 无新告警段"
        assert _rows(sid) == [], "重拉补齐 ⇒ 不得留 open 行"

    @needs_db
    def test_still_missing_lands_open_with_span(self):
        sid, exp = NS + "_b", ["20260105", "20260106", "20260107"]

        def _repull(day):
            raise RuntimeError("上游仍不可达")

        rec = self._call(sid, exp, [{"20260105"}, {"20260105"}], repull=_repull)
        assert rec["repull_failed"] == 2 and rec["gap_dates"] == ["20260106", "20260107"]
        assert rec["new_segments"] == [("2026-01-06", "2026-01-07")], \
            "本轮新 open 段＝告警依据（与 per-symbol 同纪律）"
        assert _rows(sid) == [("", "2026-01-06", "2026-01-07", "open")], "连续缺日合成一段"

    @needs_db
    def test_calendar_none_is_uncertain_not_fake_gap(self):
        """**P0-2 主判据**：`expected=None`（日历未覆盖）⇒ uncertain，**不重拉、不落 open**。"""
        sid = NS + "_c"
        calls: list[str] = []
        rec = self._call(sid, None, [set()], repull=lambda d: calls.append(d))
        assert calls == [], "uncertain 下**不得**重拉"
        assert rec["uncertain"] == "calendar_uncovered" and rec["gap_dates"] == []
        assert _rows(sid) == [("", "2026-01-01", "2026-01-31", "uncertain")]

    @needs_db
    def test_local_unreadable_is_uncertain(self):
        """本地存在性不可判（表缺/查询失败）⇒ uncertain——空集会被当成「一天都没有」= 全窗假缺口。"""
        sid = NS + "_d"
        rec = self._call(sid, ["20260105"], [None])
        assert rec["uncertain"] == "local_unreadable" and rec["gap_dates"] == []
        assert _rows(sid)[0][3] == "uncertain"

    @needs_db
    def test_budget_truncation_still_lands_open_for_rest(self):
        """预算耗尽：只重拉余量内的缺日；**余者照实落 open**（不得静默当补齐）。"""
        sid, exp = NS + "_e", ["20260105", "20260106", "20260107", "20260108"]
        calls: list[str] = []

        def _repull(day):
            calls.append(day)

        rec = self._call(sid, exp, [{"20260105"}, {"20260105"}], repull=_repull, max_repull=2)
        assert calls == ["20260106", "20260107"], "cap=2 ⇒ 只重拉前 2 个缺日"
        assert rec["missing"] == 3 and rec["gap_dates"] == ["20260106", "20260107", "20260108"]
        assert _rows(sid) == [("", "2026-01-06", "2026-01-08", "open")]

    @needs_db
    def test_empty_expected_is_not_uncertain(self):
        """窗口内本无期望日（空窗）≠ 不确定：不落 uncertain 行（两者混淆会让指标虚高）。"""
        sid = NS + "_f"
        rec = self._call(sid, [], [set()])
        assert rec["uncertain"] is None and _rows(sid) == []

    @needs_db
    def test_second_local_read_failure_after_repull_is_uncertain(self):
        """重拉后复查读取失败 ⇒ uncertain（不得拿空集推断「全补齐」）。"""
        sid, exp = NS + "_g", ["20260105", "20260106"]
        rec = self._call(sid, exp, [{"20260105"}, None], repull=lambda d: None)
        assert rec["uncertain"] == "local_unreadable"
        assert _rows(sid)[0][3] == "uncertain"


# ---------------------------------------------------------------------------
# 3：四处站点真接线（gap_dates / excluded 透传；转债宇宙；TIER1 日期列）
# ---------------------------------------------------------------------------


class _RecSpy:
    """`_reconcile_dates` 替身：记录调用入参并返回固定结构。"""

    def __init__(self, ret=None):
        self.calls: list[dict] = []
        self.ret = ret or {"expected": 2, "missing": 1, "repulled": 0, "repull_failed": 0,
                           "still_missing": ["20260106"], "gap_dates": ["20260106"],
                           "segments": [("20260106", "20260106")],
                           "new_segments": [("20260106", "20260106")],
                           "gaps": {}, "uncertain": None}

    def __call__(self, sync_id, **kw):
        self.calls.append({"sync_id": sync_id, **kw})
        return self.ret


class _FakeFrame:
    def __init__(self, rows=None):
        self.rows = rows or []


class TestStationWiring:
    def test_daily_batch_passes_coverage_aware_expected_and_returns_gap_dates(self):
        """日线批：期望集用**覆盖感知**结果（不是 freq=B 回退表）；返回体带 `gap_dates`。"""
        spy = _RecSpy()
        ad = MagicMock(provider="tushare")
        ad.fetch_supply.return_value = _FakeFrame()
        cfg = {"id": "astock_daily", "api": "pro.daily"}
        with patch.object(engine, "_reconcile_dates", spy), \
             patch.object(engine, "_trade_dates_in_range",
                          return_value=["20260105", "20260106"]) as mcal, \
             patch.object(engine, "_get_rate_ds", return_value=MagicMock()):
            r = engine._sync_via_kind_daily_batch(
                ad, sub="stock", cfg=cfg, start="20260105", end_date="20260106")
        assert mcal.called
        assert spy.calls[0]["expected"] == ["20260105", "20260106"]
        assert spy.calls[0]["win_lo"] == "20260105" and spy.calls[0]["win_hi"] == "20260106"
        assert r["gap_dates"] == ["20260106"] and r["reconcile"] is spy.ret

    def test_daily_batch_calendar_none_passes_none_not_fallback(self):
        """日历未覆盖：拉取循环回退 `freq="B"`，但**传给对账的 expected 必须是 `None`**（P0-2）。"""
        spy = _RecSpy()
        ad = MagicMock(provider="tushare")
        ad.fetch_supply.return_value = _FakeFrame()
        with patch.object(engine, "_reconcile_dates", spy), \
             patch.object(engine, "_trade_dates_in_range", return_value=None), \
             patch.object(engine, "_get_rate_ds", return_value=MagicMock()):
            engine._sync_via_kind_daily_batch(
                ad, sub="etf", cfg={"id": "etf_daily", "api": "pro.fund_daily"}, start="20260105", end_date="20260106")
        assert spy.calls[0]["expected"] is None
        assert spy.calls[0]["where_params"] == ("etf",)

    def test_cb_daily_universe_from_security_master(self):
        """裁定 Q3：转债宇宙取 SM（前缀法漏 18 只 ⇒ 不得按 symbol 前缀猜）。"""
        spy = _RecSpy()
        ad = MagicMock(provider="tushare")
        ad.fetch_supply.return_value = _FakeFrame()
        with patch.object(engine, "_reconcile_dates", spy), \
             patch.object(engine, "_trade_dates_in_range", return_value=["20260105"]), \
             patch.object(engine, "_get_rate_ds", return_value=MagicMock()):
            engine._sync_via_kind_cb_daily(
                ad, sync_id="cb_daily", start="20260105", end_date="20260105")
        where = spy.calls[0]["where"]
        assert "security_master" in where and "convertible" in where
        assert "substring" not in where and "like" not in where.lower()

    def test_tier1_date_column_follows_date_param(self):
        """TIER1：本地存在性比 `date_param` 对应的列（forecast 是 `ann_date`——列即落库列）。"""
        for sid, expected_col in (("stk_limit_sync", "trade_date"), ("forecast_sync", "ann_date")):
            ad = MagicMock()
            ad.fetch_supply.return_value = pd.DataFrame()
            spy = _RecSpy()
            with patch.object(engine, "_family_start", return_value=(date(2026, 1, 5), [])), \
                 patch.object(engine, "_data_ready_end_date", return_value="20260106"), \
                 patch.object(engine, "_trade_dates_in_range", return_value=["20260105", "20260106"]), \
                 patch.object(engine, "_reconcile_dates", spy), \
                 patch.object(engine, "_get_supply_adapter", return_value=ad), \
                 patch.object(engine, "_get_rate_ds", return_value=MagicMock()), \
                 patch.object(engine, "_provider_of", return_value="tushare"):
                h = engine._make_tier1_handler(
                    "k", None, "t", ["trade_date", "ts_code"],
                    date_param="ann_date" if sid == "forecast_sync" else "trade_date")
                h({"id": sid, "last_sync_date": "20260104", "provider": "tushare"},
                  "20260106")
            assert spy.calls[0]["date_expr"] == expected_col, sid
            assert spy.calls[0]["repull_fn"] is not None, "必须有内联重拉"

    def test_tier1_passes_family_exclusions_through(self):
        """A 股站点必须把 `_family_start` 的排除段透传（批 108 只记日志的 `excluded` 接线面）。"""
        ad = MagicMock()
        ad.fetch_supply.return_value = pd.DataFrame()
        excl = [{"kind": "policy_discard", "symbol": "", "from": "2020-01-01", "to": "2026-09-29"}]
        with patch.object(engine, "_family_start", return_value=(date(2026, 1, 5), excl)), \
             patch.object(engine, "_data_ready_end_date", return_value="20260106"), \
             patch.object(engine, "_trade_dates_in_range", return_value=[]), \
             patch.object(engine, "_reconcile_dates", _RecSpy()), \
             patch.object(engine, "_get_supply_adapter", return_value=ad), \
             patch.object(engine, "_get_rate_ds", return_value=MagicMock()), \
             patch.object(engine, "_provider_of", return_value="tushare"):
            h = engine._make_tier1_handler("k", None, "t", ["trade_date", "ts_code"])
            r = h({"id": "stk_limit_sync", "last_sync_date": "20260104", "provider": "tushare"},
                  "20260106")
        assert r["excluded"] == excl

    def test_via_kind_attaches_family_exclusions(self):
        """`_sync_via_kind` 的四个分支都要带上 `excluded`（批 108 在此处丢掉了它）。"""
        excl = [{"kind": "unreachable", "symbol": "", "from": "2019-01-01", "to": "2020-01-01"}]
        with patch.object(engine, "_read_sync_kind",
                          return_value={"kind": "bar_daily", "sub_kind": "stock",
                                        "pg_table": "bar_1d", "rebuild": False, "pk_cols": []}), \
             patch.object(engine, "_get_kline_adapter", return_value=MagicMock()), \
             patch.object(engine, "_family_start", return_value=(date(2026, 1, 5), excl)), \
             patch.object(engine, "_sync_via_kind_daily_batch",
                          return_value={"pulled": 0, "saved": 0, "start": "20260105"}):
            r = engine._sync_via_kind({"id": "astock_daily", "last_sync_date": "20260104"},
                                      "20260106")
        assert r["excluded"] == excl

    def test_via_kind_noop_window_still_carries_exclusions(self):
        """窗口已闭合的早退路径也要带 `excluded`（否则该轮排除段静默消失）。"""
        excl = [{"kind": "policy_discard", "symbol": "", "from": "2020-01-01", "to": "2026-09-29"}]
        with patch.object(engine, "_read_sync_kind",
                          return_value={"kind": "bar_daily", "sub_kind": "stock",
                                        "pg_table": "bar_1d", "rebuild": False, "pk_cols": []}), \
             patch.object(engine, "_get_kline_adapter", return_value=MagicMock()), \
             patch.object(engine, "_family_start", return_value=(date(2026, 1, 5), excl)):
            r = engine._sync_via_kind({"id": "astock_daily", "last_sync_date": "20261231"},
                                      "20260106")
        assert r["excluded"] == excl and r["pulled"] == 0
