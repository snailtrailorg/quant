"""批 97：tier1 逐日循环接交易日历（法定节假日不空跑）+ trade_cal 深度回补。

根因（威廉姆裁定「不能机械做无用调用空跑」）：
- `_make_tier1_handler` 逐日循环用 `pd.date_range(freq="B")`——只排周末，
  **法定节假日全部空调用**（2010 起约 1100+ 天，占回补调用 1/4）；
- `_sync_trade_cal` 无视 `backfill_from` 只拉当年 ⇒ trade_cal 恒 365 行，
  深回补区间无日历可用（fail-open 也只能继续空跑）。

修法：
1. `engine._trade_dates_in_range(start, end)`——区间交易日（is_open=1），带
   **覆盖度守卫**（日历首行距区间起点 >3 天 ⇒ 未覆盖，防"只有 2026 一年却回补
   2010 起"的静默半拉）；异常/空 ⇒ None。
2. handler 循环：日历可用 → 交易日迭代；不可用 → fail-open 回 freq="B"
   （宁多打、不漏拉；trade days ⊆ business days，收窄无漏拉风险）。
3. `_sync_trade_cal`：backfill_from 起逐年拉到当年（每年 1 次调用）。
"""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data_sync import engine


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


class _FakeDS:
    provider = "tushare"

    def record_usage(self, **kw):
        pass

    def get_rate_limit(self, api_name):
        return 0.0


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **kw):
        pass

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestTradeDatesInRange:
    """`_trade_dates_in_range`：区间交易日 + 覆盖度守卫 + fail-open。"""

    def test_empty_calendar_returns_none(self):
        with patch("src.data_platform.db.get_conn", return_value=_FakeConn([])):
            assert engine._trade_dates_in_range("20260101", "20261005") is None

    def test_partial_coverage_guard(self):
        """日历首行晚于区间起点 >3 天 ⇒ 未覆盖 ⇒ None（防深回补静默半拉）。"""
        rows = [(date(2026, 1, 30), False), (date(2026, 2, 2), True)]
        with patch("src.data_platform.db.get_conn", return_value=_FakeConn(rows)):
            assert engine._trade_dates_in_range("20260101", "20261231") is None

    def test_normal_returns_open_days_only(self):
        rows = [(date(2026, 9, 28), True), (date(2026, 9, 29), True),
                (date(2026, 10, 1), False), (date(2026, 10, 2), False),
                (date(2026, 10, 5), False)]
        with patch("src.data_platform.db.get_conn", return_value=_FakeConn(rows)):
            # 首行 09-28 距起点 09-26 仅 2 天 ⇒ 覆盖成立
            assert engine._trade_dates_in_range("20260926", "20261009") == \
                ["20260928", "20260929"]

    def test_db_error_failopen_none(self):
        with patch("src.data_platform.db.get_conn", side_effect=RuntimeError("db down")):
            assert engine._trade_dates_in_range("20260101", "20261231") is None

    @needs_db
    def test_real_db_2026_covered(self):
        """真库（2026 日历在位）⇒ 2026 区间返回非空交易日列表。"""
        got = engine._trade_dates_in_range("20260101", "20261005")
        assert got and all(g <= "20261005" for g in got)

    @needs_db
    def test_real_db_uncovered_range_fails_open(self):
        """日历未覆盖区间 ⇒ None（fail-open，宁多打不漏拉）。

        批 99 修正：本钉原为「真库只有 2026 日历 ⇒ 2010-2020 未覆盖 ⇒ None」——那是对
        **可变环境状态**取断言，`trade_cal` 深回补（1990 起）之后前提出错（现 2010-2020 已覆盖）。
        改为**自适配**：取真库最早日历日向前推 30 天作区间起点（首行距起点 >3 天 ⇒ 触发
        覆盖度守卫），无论日历回补到多深都成立。
        """
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            mn = conn.execute("SELECT min(cal_date) FROM trade_cal").fetchone()[0]
        if mn is None:
            pytest.skip("trade_cal 为空，推导不出未覆盖区间")
        start = (mn - timedelta(days=30)).strftime("%Y%m%d")
        assert engine._trade_dates_in_range(start, mn.strftime("%Y%m%d")) is None


class TestTier1TradeDayLoop:
    """handler 循环：日历可用走交易日（节假日零调用）；不可用 fail-open 回 freq=B。"""

    def _build(self, trade_days, ready="20261009", last="20260929"):
        calls: list = []

        def fake_pull(trade_date=None):
            calls.append(trade_date)
            return pd.DataFrame()

        class _FakeAdapter:
            provider = "tushare"

            def fetch_supply(self, kind, sub_kind=None, **kw):
                return fake_pull(**kw)

        with patch.object(engine, "_data_ready_end_date", return_value=ready), \
             patch.object(engine, "_trade_dates_in_range",
                          return_value=trade_days), \
             patch.object(engine, "_get_rate_ds", return_value=_FakeDS()), \
             patch.object(engine, "_get_supply_adapter", return_value=_FakeAdapter()), \
             patch("src.data_platform.rate_limit.rate_limit_context", MagicMock()), \
             patch("src.data_platform.db.get_conn", MagicMock()):
            h = engine._make_tier1_handler(
                "featured_daily", "margin_detail", "margin_detail", ["trade_date", "ts_code"],
                float_cols=["rzye"], text_cols=[], lag_trade_days=1)
            r = h({"id": "margin_detail_sync", "provider": "tushare",
                   "last_sync_date": last}, "20261010")
        return r, calls

    def test_trade_days_used_holidays_skipped(self):
        """国庆 2026-10-01~10-08 休市：freq=B 会打 6 发空枪，交易日迭代零调用。"""
        r, calls = self._build(["20260930", "20261009"])
        assert calls == ["20260930", "20261009"], calls
        assert r["expected_days"] == 2 and r["failed_dates"] == []

    def test_failopen_business_days_when_no_calendar(self):
        """日历不可用（None）⇒ 回退 freq="B"（宁多打、不漏拉）。"""
        r, calls = self._build(None)
        exp = [d.strftime("%Y%m%d")
               for d in pd.date_range(start="20260927", end="20261009", freq="B")]
        assert calls == exp
        assert r["expected_days"] == len(exp)


class TestTradeCalDeepBackfill:
    """`_sync_trade_cal`：backfill_from 起逐年拉到当年（每年 1 次调用）。"""

    def test_backfill_loops_years(self):
        years: list = []

        def fake_pull(year):
            years.append(year)
            return list(range(365))

        with patch("src.data_platform.adapters.tushare_adapter.pull_trade_cal", fake_pull), \
             patch("src.data_platform.rate_limit.rate_limit_context", MagicMock()), \
             patch.object(engine, "_get_rate_ds", return_value=_FakeDS()), \
             patch.object(engine, "_provider_of", return_value="tushare"):
            r = engine._sync_trade_cal(
                {"id": "trade_cal", "provider": "tushare"}, "20261005",
                backfill_from="20220101")
        assert years == [2022, 2023, 2024, 2025, 2026]
        assert r["pulled"] == 365 * 5 and r["failed_dates"] == []

    def test_no_backfill_single_year(self):
        years: list = []

        def fake_pull(year):
            years.append(year)
            return list(range(365))

        with patch("src.data_platform.adapters.tushare_adapter.pull_trade_cal", fake_pull), \
             patch("src.data_platform.rate_limit.rate_limit_context", MagicMock()), \
             patch.object(engine, "_get_rate_ds", return_value=_FakeDS()), \
             patch.object(engine, "_provider_of", return_value="tushare"):
            engine._sync_trade_cal(
                {"id": "trade_cal", "provider": "tushare"}, "20261005")
        assert years == [date.today().year]
