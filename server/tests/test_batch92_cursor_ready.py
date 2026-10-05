"""批 92：同步游标语义根因修复——窗口上界接交易日历（数据可用日）+ `cursor_upto` 契约。

根因：窗口上界取 `date.today()`，而按 trade_date 的增量数据**当日/T+1 前不可得** ⇒ 逐日
循环的前沿日必然空拉；下游又无条件把游标推到 end_date ⇒ **该前沿日永不复访 = 静默永久缺失**。
确定性受害样本＝`margin_detail`（cron 09:00、T+1 数据）⇒ 全表恒空。

两处修复的钉：
1. `_data_ready_end_date(lag)`：上界＝最近「已可用」交易日（真库 trade_cal + fail-open 回退）。
2. `_make_tier1_handler(..., lag_trade_days=N)`：窗口上界用它、并把 `cursor_upto` 回传给
   `sync()`（`_advance = (r.get("cursor_upto") or end_date, ...)`，缺省零回归）。
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


class TestDataReadyEndDate:
    @needs_db
    def test_lag0_is_latest_open_day_not_after_today(self):
        """lag=0 ⇒ 最近一个开盘日（含今日，若今日开市）——与真库 trade_cal 一致。"""
        with engine.get_conn() as conn:
            exp = conn.execute(
                "SELECT to_char(cal_date,'YYYYMMDD') FROM trade_cal WHERE exchange='SSE' "
                "AND is_open=1 AND cal_date <= %s ORDER BY cal_date DESC LIMIT 1",
                (date.today(),)).fetchone()[0]
        assert engine._data_ready_end_date(0) == exp

    @needs_db
    def test_lag1_strictly_before_today(self):
        """lag=1 ⇒ 严格早于今日（T+1 表上界退一个交易日）。"""
        assert engine._data_ready_end_date(1) < date.today().strftime("%Y%m%d")

    def test_failopen_natural_day_never_raises(self):
        """日历不可用 ⇒ 自然日回退，绝不抛；lag=0 保旧行为（today）。"""
        with patch.object(engine, "get_conn", side_effect=RuntimeError("no trade_cal")):
            assert engine._data_ready_end_date(0) == date.today().strftime("%Y%m%d")
            assert engine._data_ready_end_date(1) == \
                (date.today() - timedelta(days=1)).strftime("%Y%m%d")


class _FakeDS:
    provider = "tushare"

    def get_rate_limit(self, api_name):
        return 0.0


class TestTier1ReadyWindow:
    """`_make_tier1_handler` 实测（不 mock 被测逻辑，只替身源拉取与限速）。"""

    def _build(self, lag, ready="20260930", last="20260928"):
        calls: list = []

        def fake_pull(trade_date=None):
            calls.append(trade_date)
            return pd.DataFrame()

        class _FakeAdapter:
            provider = "tushare"

            def fetch_supply(self, kind, sub_kind=None, **kw):
                return fake_pull(**kw)

        with patch.object(engine, "_data_ready_end_date", return_value=ready), \
             patch.object(engine, "_get_rate_ds", return_value=_FakeDS()), \
             patch.object(engine, "_get_supply_adapter", return_value=_FakeAdapter()), \
             patch("src.data_platform.rate_limit.rate_limit_context", MagicMock()), \
             patch("src.data_platform.db.get_conn", MagicMock()):
            h = engine._make_tier1_handler(
                "featured_daily", "margin_detail", "margin_detail", ["trade_date", "ts_code"],
                float_cols=["rzye"], text_cols=[], lag_trade_days=lag)
            r = h({"id": "margin_detail_sync", "provider": "tushare",
                   "last_sync_date": last}, "20261004")
        return r, calls

    def test_upper_is_ready_date_and_cursor_upto_returned(self):
        """窗口只覆盖到「可用日」，且 `cursor_upto` 回传该日（= 下游游标推进目标）。"""
        r, calls = self._build(1)
        assert r["cursor_upto"] == "20260930"
        assert calls, "窗口为空——上界算错"
        assert all(c <= "20260930" for c in calls), f"拉到了不可得的未来日: {calls}"

    def test_registry_declares_margin_detail_lag_one(self):
        """注册表把 margin_detail 声明为 T+1（lag=1）——防有人只改工厂忘了接线。"""
        assert engine._TIER1_LAG_TRADING_DAYS.get("margin_detail_sync") == 1

    def test_overlap_replays_recent_days(self):
        """回看重叠：游标 20260928（周日）⇒ 窗口起点回看 3 天到 20260926，覆盖 28/29/30。"""
        _, calls = self._build(1, last="20260928")
        assert calls == ["20260928", "20260929", "20260930"], calls


class TestSyncCursorUptoContract:
    """`sync()` 层：`cursor_upto` 收窄游标推进（缺省 end_date = 零回归）。"""

    class _FakeLock:
        acquired = True

        def __init__(self, sid):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _run(self, ret):
        fake_cfg = {"id": "margin_detail_sync", "name": "x", "mode": "incremental",
                    "enabled": True, "last_sync_date": "20260928", "last_sync_ts": None,
                    "last_status": "idle"}
        updates: list = []
        with patch("src.data_sync.sync_lock.SyncLock", self._FakeLock), \
             patch.object(engine, "_get_config", return_value=fake_cfg), \
             patch.object(engine, "_VIA_KIND_IDS", frozenset()), \
             patch.object(engine, "_HANDLERS", {"margin_detail_sync": MagicMock(return_value=ret)}), \
             patch.object(engine, "_log"), \
             patch.object(engine, "_mark_running"), \
             patch.object(engine, "_update_sync_state",
                          side_effect=lambda *a, **k: updates.append(a)):
            result = engine.sync("margin_detail_sync")
        return result, updates

    def test_cursor_upto_narrows_advance(self):
        """回传 cursor_upto 时游标推到它（可窄于 end_date=today）——防推过不可得的前沿日。"""
        ret = {"pulled": 0, "saved": 0, "start": "20260929", "cursor_upto": "20260930",
               "failed_dates": []}
        _, updates = self._run(ret)
        assert updates[-1] == ("margin_detail_sync", "20260930", 0, "idle")

    def test_absent_cursor_upto_keeps_old_behavior(self):
        """未声明 cursor_upto 的 handler ⇒ 仍推进到 end_date（零回归，分钟线等不受影响）。"""
        ret = {"pulled": 1, "saved": 1, "start": "20260929", "failed_dates": []}
        _, updates = self._run(ret)
        assert updates[-1][1] == date.today().strftime("%Y%m%d")
