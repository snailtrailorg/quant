"""批 58·M3 通用引擎循环 `_sync_via_kind` 测试钉（bar 族收编）。

钉什么：①粒度分派（逐日/区间/per-symbol）与返回 dict 形状（last_success_date 有无=三态游标）
②_fetch_supply 构造 supply DataRequest（不走 resolve）③_sync_kind_whitelist 读灰度白名单
④落仓分叉（_save_bars/save_index_bars/save_bars(freq)）⑤非 bar 族兜底 UnsupportedFeature。
"""
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src.data_sync import engine
from src.data_platform.adapters.base import UnsupportedFeature


def _frame_mock(rows=("r1", "r2")):
    f = MagicMock()
    f.rows = list(rows)
    return f


def _cfg(sync_id="astock_daily"):
    return {"id": sync_id, "provider": "tushare", "last_sync_date": None,
            "api": "pro.daily", "mode": "incremental", "enabled": True}


def _conn_mock(value):
    """get_conn 上下文 mock：execute→fetchone 返回单列 (value,)。"""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.fetchone.return_value = None if value is None else (value,)
    conn.execute.return_value = cur
    return conn


# ——— _fetch_supply：supply 请求构造（不走 resolve） ———

class TestFetchSupply:
    def test_supply_request_shape(self):
        ad = MagicMock()
        engine._fetch_supply(ad, kind="bar_daily", sub_kind="stock", symbols=(),
                             start="20260901", end="20260901", freq="1D")
        req = ad.fetch.call_args.args[0]
        assert req.kind == "bar_daily"
        assert req.sub_kind == "stock"
        assert req.mode == "supply"
        assert req.freq == "1D"
        assert req.consumer_tag == "sync"
        assert req.symbols == ()

    def test_index_symbols_passed(self):
        ad = MagicMock()
        engine._fetch_supply(ad, kind="index_daily", sub_kind=None,
                             symbols=("000300.SH",), start="20050408", end="20260921", freq="1D")
        assert ad.fetch.call_args.args[0].symbols == ("000300.SH",)


# ——— _sync_via_kind 粒度分派 ———

class TestDispatch:
    def _patch_dispatch(self):
        return patch.object(engine, "_read_sync_kind"), patch.object(engine, "_get_kline_adapter")

    def test_daily_batch_stock(self):
        with patch.object(engine, "_read_sync_kind", return_value={
                "kind": "bar_daily", "sub_kind": "stock", "pg_table": "bar_1d", "rebuild": "incremental"}), \
             patch.object(engine, "_get_kline_adapter", return_value=MagicMock()) as ga, \
             patch.object(engine, "_sync_via_kind_daily_batch", return_value={"x": 1}) as db:
            r = engine._sync_via_kind(_cfg("astock_daily"), "20260921", backfill_from="20260901")
        assert r == {"x": 1}
        db.assert_called_once_with(ga.return_value, sub="stock", cfg=_cfg("astock_daily"),
                                   start="20260901", end_date="20260921", progress_cb=None)

    def test_daily_batch_convertible_interval(self):
        with patch.object(engine, "_read_sync_kind", return_value={
                "kind": "bar_daily", "sub_kind": "convertible", "pg_table": "bar_1d", "rebuild": "incremental"}), \
             patch.object(engine, "_get_kline_adapter", return_value=MagicMock()) as ga, \
             patch.object(engine, "_sync_via_kind_cb_daily", return_value={"x": 2}) as cb:
            r = engine._sync_via_kind(_cfg("cb_daily"), "20260921", backfill_from="20260901")
        assert r == {"x": 2}
        cb.assert_called_once_with(ga.return_value, start="20260901", end_date="20260921", progress_cb=None)

    def test_index_daily(self):
        with patch.object(engine, "_read_sync_kind", return_value={
                "kind": "index_daily", "sub_kind": None, "pg_table": "bar_index", "rebuild": "incremental"}), \
             patch.object(engine, "_get_kline_adapter", return_value=MagicMock()) as ga, \
             patch.object(engine, "_sync_via_kind_index", return_value={"x": 3}) as idx:
            r = engine._sync_via_kind(_cfg("index_daily"), "20260921")
        assert r == {"x": 3}
        idx.assert_called_once_with(ga.return_value, start="20050408", end_date="20260921", progress_cb=None)

    def test_minute(self):
        with patch.object(engine, "_read_sync_kind", return_value={
                "kind": "bar_minute", "sub_kind": None, "pg_table": "bar_1min", "rebuild": "incremental"}), \
             patch.object(engine, "_get_kline_adapter", return_value=MagicMock()) as ga, \
             patch.object(engine, "_sync_via_kind_minute", return_value={"x": 4}) as mn:
            r = engine._sync_via_kind(_cfg("astock_minute"), "20260921", backfill_from="20260901")
        assert r == {"x": 4}
        mn.assert_called_once_with(ga.return_value, sync_id="astock_minute",
                                   start="20260901", end_date="20260921", progress_cb=None)

    def test_non_bar_unsupported(self):
        with patch.object(engine, "_read_sync_kind", return_value={
                "kind": "fundamental_daily", "sub_kind": None, "pg_table": "daily_basic", "rebuild": "incremental"}), \
             patch.object(engine, "_get_kline_adapter", return_value=MagicMock()):
            with pytest.raises(UnsupportedFeature):
                engine._sync_via_kind(_cfg("astock_basic"), "20260921", backfill_from="20260901")

    def test_no_config_row_raises(self):
        """无归置行必须 raise（sync() 不查 status，return error 会被当成功推进游标=数据丢失）。"""
        with patch.object(engine, "_read_sync_kind", return_value={}):
            with pytest.raises(RuntimeError):
                engine._sync_via_kind(_cfg("unknown"), "20260921")


# ——— _sync_via_kind_daily_batch：逐日三态 ———

class TestDailyBatch:
    def _patch_loop(self):
        return (
            patch.object(engine, "_get_rate_ds", return_value=MagicMock()),
            patch("src.data_platform.rate_limit.rate_limit_context", return_value=MagicMock()),
            patch.object(engine, "_expected_trading_days", return_value=3),
            patch.object(engine, "_save_bars", return_value=2),
        )

    def test_all_days_success_three_state(self):
        with self._patch_loop()[0], self._patch_loop()[1], self._patch_loop()[2], self._patch_loop()[3], \
             patch.object(engine, "_fetch_supply", return_value=_frame_mock()) as fs:
            r = engine._sync_via_kind_daily_batch(MagicMock(), sub="stock", cfg=_cfg(),
                                                  start="20260901", end_date="20260903")
        # 3 个交易日 → 3 次 fetch；全成功 → last_success_date=末日，failed 空
        assert fs.call_count == 3
        assert r["last_success_date"] == "20260903"
        assert r["failed_dates"] == []
        assert r["pulled"] == 2 * 3 and r["saved"] == 2 * 3

    def test_mid_failure_freezes_last_success(self):
        calls = {"n": 0}

        def _fetch(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            return _frame_mock()

        with self._patch_loop()[0], self._patch_loop()[1], self._patch_loop()[2], self._patch_loop()[3], \
             patch.object(engine, "_fetch_supply", side_effect=_fetch):
            r = engine._sync_via_kind_daily_batch(MagicMock(), sub="etf", cfg=_cfg(),
                                                  start="20260901", end_date="20260903")
        assert r["last_success_date"] == "20260901"   # 连续成功末=失败前一日
        assert len(r["failed_dates"]) == 1


# ——— 落仓分叉 ———

class TestLanding:
    def test_index_saves_index_bars(self):
        with patch.object(engine, "_fetch_supply", return_value=_frame_mock()), \
             patch("src.data_platform.db.save_index_bars", return_value=2) as sb:
            r = engine._sync_via_kind_index(MagicMock(), start="20050408", end_date="20260921")
        sb.assert_called_once()
        assert "last_success_date" not in r   # 无条件推进

    def test_minute_saves_bars_per_freq(self):
        with patch.object(engine, "_get_rate_ds", return_value=MagicMock()), \
             patch("src.data_platform.rate_limit.rate_limit_context", return_value=MagicMock()), \
             patch.object(engine, "_list_static_ts_codes", return_value=["600000.SH"]), \
             patch.object(engine, "_fetch_supply", return_value=_frame_mock()), \
             patch("src.data_platform.db.save_bars", return_value=2) as sb:
            r = engine._sync_via_kind_minute(MagicMock(), sync_id="astock_minute_5min",
                                             start="20260901", end_date="20260921")
        sb.assert_called_once()   # freq 从 _MINUTE_FREQ 取 5min
        assert sb.call_args.args[0] == "5min"
        assert "last_success_date" not in r


# ——— 灰度白名单 ———

class TestWhitelist:
    def test_parses_comma_list(self):
        with patch.object(engine, "get_conn", return_value=_conn_mock("astock_daily, etf_daily")):
            assert engine._sync_kind_whitelist() == {"astock_daily", "etf_daily"}

    def test_empty_off(self):
        with patch.object(engine, "get_conn", return_value=_conn_mock(None)):
            assert engine._sync_kind_whitelist() == set()

    def test_db_error_off(self):
        with patch.object(engine, "get_conn", side_effect=RuntimeError("down")):
            assert engine._sync_kind_whitelist() == set()
