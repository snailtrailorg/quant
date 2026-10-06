"""批 58·M3 通用引擎循环 `_sync_via_kind` 测试钉（bar 族收编）。

钉什么：①粒度分派（逐日/区间/per-symbol）与返回 dict 形状（last_success_date 有无=三态游标）
②_fetch_supply 构造 supply DataRequest（不走 resolve）③批 72：白名单机制退役——静态路由 _VIA_KIND_IDS
④落仓分叉（_save_bars/save_index_bars/save_bars(freq)）⑤非 bar 族兜底 UnsupportedFeature。
"""
import os
from datetime import date, datetime
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src.data_sync import engine
from src.data_platform.adapters.base import UnsupportedFeature


def _adapter(src_lo="2010-01-01", lag=0):
    """批 108·步 3：`_sync_via_kind` 起手取家族地板 ⇒ 桩 adapter 须给**真元组**
    （MagicMock 默认返回 MagicMock，解包即 `ValueError`）。"""
    ad = MagicMock()
    ad.available_range.return_value = (src_lo, None)
    ad.publish_lag.return_value = lag
    return ad


def _frame_mock(rows=("r1", "r2")):
    f = MagicMock()
    f.rows = list(rows)
    return f


def _cfg(sync_id="astock_daily", **extra):
    d = {"id": sync_id, "provider": "tushare", "last_sync_date": None,
         "api": "pro.daily", "mode": "incremental", "enabled": True}
    d.update(extra)
    return d


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
             patch.object(engine, "_get_kline_adapter", return_value=_adapter()) as ga, \
             patch.object(engine, "_sync_via_kind_daily_batch", return_value={"x": 1}) as db:
            r = engine._sync_via_kind(_cfg("astock_daily"), "20260921", backfill_from="20260901")
        assert r == {"x": 1}
        db.assert_called_once_with(ga.return_value, sub="stock", cfg=_cfg("astock_daily"),
                                   start="20260901", end_date="20260921", progress_cb=None)

    def test_daily_batch_convertible_interval(self):
        with patch.object(engine, "_read_sync_kind", return_value={
                "kind": "bar_daily", "sub_kind": "convertible", "pg_table": "bar_1d", "rebuild": "incremental"}), \
             patch.object(engine, "_get_kline_adapter", return_value=_adapter()) as ga, \
             patch.object(engine, "_sync_via_kind_cb_daily", return_value={"x": 2}) as cb:
            r = engine._sync_via_kind(_cfg("cb_daily"), "20260921", backfill_from="20260901")
        assert r == {"x": 2}
        cb.assert_called_once_with(ga.return_value, start="20260901", end_date="20260921", progress_cb=None)

    def test_index_daily(self):
        with patch.object(engine, "_read_sync_kind", return_value={
                "kind": "index_daily", "sub_kind": None, "pg_table": "bar_index", "rebuild": "incremental"}), \
             patch.object(engine, "_get_kline_adapter", return_value=_adapter(src_lo=None)) as ga, \
             patch.object(engine, "_sync_via_kind_index", return_value={"x": 3}) as idx:
            # 批 108·步 3：`20050408` 从**硬编码**改为 `retention` 列承载（值不变、来源正名）
            r = engine._sync_via_kind(
                _cfg("index_daily", retention=date(2005, 4, 8)), "20260921")
        assert r == {"x": 3}
        idx.assert_called_once_with(ga.return_value, start="20050408", end_date="20260921", progress_cb=None)

    def test_minute(self):
        with patch.object(engine, "_read_sync_kind", return_value={
                "kind": "bar_minute", "sub_kind": None, "pg_table": "bar_1min", "rebuild": "incremental"}), \
             patch.object(engine, "_get_kline_adapter", return_value=_adapter()) as ga, \
             patch.object(engine, "_sync_via_kind_minute", return_value={"x": 4}) as mn:
            r = engine._sync_via_kind(_cfg("astock_minute"), "20260921", backfill_from="20260901")
        assert r == {"x": 4}
        mn.assert_called_once_with(ga.return_value, sync_id="astock_minute",
                                   start="20260901", end_date="20260921", progress_cb=None)

    def test_non_bar_unsupported(self):
        with patch.object(engine, "_read_sync_kind", return_value={
                "kind": "fundamental_daily", "sub_kind": None, "pg_table": "daily_basic", "rebuild": "incremental"}), \
             patch.object(engine, "_get_kline_adapter", return_value=_adapter()):
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





def test_via_kind_ids_mutex_with_handlers():
    """防双注册钉（批 72 双盲 A P1-2）：_VIA_KIND_IDS 与 _HANDLERS 全量 14 键
    （字面量 5+tier1 工厂 9）零交集——未来回填 bar 族键成路由双源即红。"""
    assert not (engine._VIA_KIND_IDS & set(engine._HANDLERS))
    assert len(engine._VIA_KIND_IDS) == 6


class TestRealFetchPath:
    """批 72 双盲 B P0-1 回归钉：真调 _fetch_supply 走全程（不 mock——sub_kind required
    kwarg 缺参 TypeError 被 mock 形状屏蔽的教训，fake 形状差假绿同型）。"""

    def test_index_full_path_no_typeerror(self):
        adapter = MagicMock()
        adapter.fetch.return_value = MagicMock(rows=["r1", "r2"])
        with patch("src.data_platform.db.save_index_bars", return_value=2) as ms:
            r = engine._sync_via_kind_index(adapter, start="20260901", end_date="20260921")
        assert r["pulled"] == 2 and r["saved"] == 2
        ms.assert_called_once_with(["r1", "r2"])

    def test_minute_full_path_no_typeerror(self):
        adapter = MagicMock()
        adapter.fetch.return_value = MagicMock(rows=["r1"])
        adapter.provider = "tushare"
        with patch.object(engine, "_list_static_ts_codes", return_value=["600000.SH"]), \
             patch("src.data_platform.db.save_bars", return_value=1):
            r = engine._sync_via_kind_minute(adapter, sync_id="astock_minute",
                                             start="20260901", end_date="20260921")
        assert r["pulled"] == 1 and r["saved"] == 1
        assert not r["failed_dates"]


def test_fetch_and_save_quality_label_per_symbol():
    """批 72 双盲 P2-3：per-symbol 挂钩 label 映射钉（_fetch_and_save kind=astock→bar_daily）。"""
    df = MagicMock()
    df.empty = False
    df.columns = ["trade_date"]
    adapter = MagicMock()
    adapter.pull_daily.return_value = df
    adapter.to_bar_rows.return_value = ["r1"]
    with patch.object(engine, "_log_bar_quality") as mq, \
         patch.object(engine, "_save_bars", return_value=1):
        engine._fetch_and_save(adapter, "600000.SH", "20260901", "20260921",
                               save_fn=lambda rows: None, kind="astock")
    mq.assert_called_once_with(df, "bar_daily")
