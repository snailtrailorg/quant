"""批 95：单日表窗口——最近交易日回落 + full 无参 45 天 + 陈旧守卫。

三组：①_window_of 纯函数 oracle（三态：无参/回落/full 无参）②_nearest_trade_day 四态
（命中/无行/异常/陈旧）③run_pool_sync 集成（full ⇒ cyq_chips range_ is None）。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.data_sync import pool_data as PD


class TestWindowPure:
    def test_default_is_legacy(self):
        """缺省零漂移：(upper, upper)。"""
        assert PD._window_of("cyq_chips", {}, "20261018") == ("20261018", "20261018")

    def test_single_day_fallback(self):
        """日常轮：回落最近交易日（A/B 共同修法）。"""
        assert PD._window_of("cyq_chips", {}, "20261018", single_day="20261016") \
            == ("20261016", "20261016")

    def test_single_day_all_no_param(self):
        """full/回补轮：无参 ⇒ 一次 ~45 天（A-P0-1 实测）。"""
        assert PD._window_of("cyq_chips", {}, "20261018", single_day_all=True) == (None, None)

    def test_incremental_unchanged(self):
        """增量表上界仍自然日（批 92 裁定不动——防公告日漏拉）。"""
        assert PD._window_of("income", {"income": "20261010"}, "20261018", 7) \
            == ("20261003", "20261018")


class TestNearestTradeDay:
    def _mock_conn(self, row):
        m = MagicMock()
        m.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = row
        return m

    def test_hit(self):
        with patch.object(PD._pdb, "get_conn", self._mock_conn(("20261016",))):
            assert PD._nearest_trade_day("20261018") == "20261016"

    def test_no_row_fail_open(self, caplog):
        with patch.object(PD._pdb, "get_conn", self._mock_conn(None)):
            assert PD._nearest_trade_day("20261018") == "20261018"
        assert "未覆盖" in caplog.text   # 无行须留痕（不得误诊成 DB 异常）

    def test_exception_fail_open(self):
        m = MagicMock()
        m.side_effect = RuntimeError("db down")
        with patch.object(PD._pdb, "get_conn", m):
            assert PD._nearest_trade_day("20261018") == "20261018"

    def test_stale_guard(self):
        """陈旧守卫（A-P1-2）：跨度 >10 日返原值——防 trade_cal 断更静默拉陈旧日。"""
        with patch.object(PD._pdb, "get_conn", self._mock_conn(("20260101",))):
            assert PD._nearest_trade_day("20261018") == "20261018"


class TestRunIntegration:
    def test_full_uses_paramless_window(self):
        """full 轮：cyq_chips 请求 range_ is None（原验收只断言最近交易日，证明不了 full 差异）。"""
        seen = []

        class _Ad:
            def fetch(self, req):
                seen.append((req.sub_kind, req.range_))
                import pandas as pd
                return pd.DataFrame()

        with patch.object(PD, "_get_pool_ts_codes", return_value=["600000.SH"]), \
             patch.object(PD, "_read_sync_kind", return_value={
                 "kind": "featured_daily", "sub_kind": "cyq_chips", "pg_table": "cyq_chips",
                 "rebuild": "incremental", "pk_cols": ["ts_code", "trade_date"],
                 "float_cols": [], "text_cols": [], "overlap_days": None, "lag_trade_days": None}), \
             patch.object(PD, "_get_kline_adapter", return_value=_Ad()), \
             patch.object(PD, "_get_rate_ds"), \
             patch.object(PD, "_load_cursors", return_value={}), \
             patch.object(PD, "_sink_frame", return_value=1), \
             patch.object(PD, "_nearest_trade_day", return_value="20261016"):
            PD.run_pool_sync(cfg={"id": "pool_data", "provider": "tushare"},
                             full=True, symbols=["600000.SH"], timebox_s=30)
        cyq = [rng for sub, rng in seen if sub == "cyq_chips"]
        assert cyq and all(r is None for r in cyq), cyq
