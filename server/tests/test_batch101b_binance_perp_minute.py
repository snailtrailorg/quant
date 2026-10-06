"""批 101b：币安永续盘中 bar（小时 / 1min / 15min）——interval 泛化 + handler 工厂。

覆盖三件容易漂移的事：
1. **interval 映射是 adapter 的单源**（`_INTERVAL_BY_FREQ`）；`pull_minute` 只认内部 freq，
   源 interval 名（`15m`）与日粒度都**响亮拒绝**（放行会把调用方 bug 变成脏表脏列）。
2. **handler 工厂的游标契约**：`cursor_upto` ＝ 实际取到数据的最后一日（**不是窗口上界**）
   —— 批量站有 >1 天发布滞后，按窗口上界推会**吃日成洞**（本批修的真缺陷，防回退）。
3. **配置面与代码声明一致**：`sync_config`/`sync_kind_config` 的新三行与
   `engine._BINANCE_BAR_SPECS` 逐项对账；**`schedule='manual'` 是存储闸门**（防被"顺手"改成
   cron ⇒ 1min 每天 450MB、8.3G 余量约 18 天打满）。

离线（无 dev 库/无网）为默认；真库对账类 skipif 自动跳过。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from src.data_platform.adapters import binance_adapter as BA
from src.data_platform.adapters.base import UnsupportedFeature

# ---------------------------------------------------------------------------
# 1：interval 映射与 pull_minute 严格性
# ---------------------------------------------------------------------------


class TestIntervalMapping:
    def test_internal_freq_maps_to_interval(self):
        a = BA.BinanceAdapter()
        assert a.to_source_freq("1min") == "1m"
        assert a.to_source_freq("15min") == "15m"
        assert a.to_source_freq("1h") == "1h"
        assert a.to_source_freq("1D") == "1d"

    def test_freq_is_table_suffix(self):
        """freq 同时是表名后缀（`bar_{freq.lower()}`）——specs 的自洽基础。"""
        for f in ("1h", "1min", "15min"):
            assert f"bar_{f.lower()}" in ("bar_1h", "bar_1min", "bar_15min")

    def test_kline_url_carries_interval(self):
        url = BA._kline_url("BTCUSDT", "1m", "daily", "2026-10-04")
        assert "/klines/BTCUSDT/1m/BTCUSDT-1m-2026-10-04.zip" in url
        assert BA._kline_url("BTCUSDT", "1h", "monthly", "2026-09").endswith(
            "/klines/BTCUSDT/1h/BTCUSDT-1h-2026-09.zip")

    @pytest.mark.parametrize("freq", ["15m", "1m", "1h_", "1D", "5min", "1d", ""])
    def test_pull_minute_rejects_non_internal_freq(self, freq):
        """源 interval 名 / 日粒度 / 未映射 → 响亮拒绝（不是空帧）。"""
        a = BA.BinanceAdapter()
        with pytest.raises(UnsupportedFeature):
            a.pull_minute("BTCUSDT.BINANCE", freq, "20261004", "20261004")

    def test_pull_minute_uses_mapped_interval(self):
        """`pull_minute('15min')` 必须去打 `15m` 目录（而不是 15min/1m）。"""
        seen: list[str] = []

        def _fake_get(url, timeout=30):
            seen.append(url)
            return None          # 全 404 ⇒ 空帧，但 URL 已留证

        a = BA.BinanceAdapter()
        with patch.object(BA, "_get", _fake_get):
            out = a.pull_minute("BTCUSDT.BINANCE", "15min", "20261003", "20261004")
        assert out.empty
        assert len(seen) == 2, seen
        assert all("/klines/BTCUSDT/15m/" in u for u in seen), seen

    def test_pull_minute_tolerates_time_suffix(self):
        """基类分钟契约给 `'YYYYMMDD HH:MM:SS'`——本源只取日期段，不得因此炸。"""
        seen: list[str] = []

        def _fake_get(url, timeout=30):
            seen.append(url)
            return None

        a = BA.BinanceAdapter()
        with patch.object(BA, "_get", _fake_get):
            a.pull_minute("BTCUSDT.BINANCE", "1min", "20261004 09:00:00", "20261004 15:00:00")
        assert all("-2026-10-04.zip" in u for u in seen), seen

    def test_fetch_supply_dispatches_minute(self):
        a = BA.BinanceAdapter()
        with patch.object(a, "pull_minute", return_value=pd.DataFrame()) as pm:
            a.fetch_supply("bar_minute", "perp", symbol="BTCUSDT", start="20261004",
                           end="20261004", freq="1h")
        assert pm.call_args[0] == ("BTCUSDT", "1h", "20261004", "20261004")

    def test_fetch_supply_unknown_kind_still_loud(self):
        with pytest.raises(UnsupportedFeature):
            BA.BinanceAdapter().fetch_supply("depth", "perp")


# ---------------------------------------------------------------------------
# 2：能力声明与注册
# ---------------------------------------------------------------------------


class TestCapabilityAndRegistry:
    def test_capabilities_include_minute_family(self):
        caps = BA.BinanceAdapter.capabilities
        assert {"binance_perp_hourly", "binance_perp_1min", "binance_perp_15min"} <= caps

    def test_decls_have_bar_minute_and_no_adj_factor(self):
        """1h/1min/15min 同归 `bar_minute`（27 词无 hour 粒度词）；**不得**声明 adj_factor。"""
        decls = BA.BinanceAdapter.capability_decls
        kinds = {(d.kind, d.temporality) for d in decls}
        assert ("bar_minute", "historical") in kinds
        assert not any(d.kind == "adj_factor" for d in decls), \
            "加密无复权概念——声明 adj_factor＝让前端把该能力挂到币安名下（虚报）"
        for d in decls:
            assert d.sub_kinds == frozenset(), "非聚合域 sub_kinds 必须为空"

    def test_validate_decls_passes(self):
        from src.quant_common.contract import validate_capability_decls
        assert validate_capability_decls(BA.BinanceAdapter.capability_decls) == []

    def test_specs_keys_are_registered_handlers(self):
        from src.data_sync.engine import _BINANCE_BAR_SPECS, _HANDLERS, _VIA_KIND_IDS
        assert set(_BINANCE_BAR_SPECS) == {
            "binance_perp_daily", "binance_perp_hourly",
            "binance_perp_1min", "binance_perp_15min"}
        assert set(_BINANCE_BAR_SPECS) <= set(_HANDLERS)
        assert not (set(_BINANCE_BAR_SPECS) & set(_VIA_KIND_IDS)), "单源路由：不得双注册"

    def test_cap_map_covers_new_ids(self):
        from src.quant_common.markets import CAPABILITIES, SYNC_ID_CAP_MAP
        for sid in ("binance_perp_hourly", "binance_perp_1min", "binance_perp_15min"):
            assert SYNC_ID_CAP_MAP[sid] == "hist_quote"
            assert SYNC_ID_CAP_MAP[sid] in CAPABILITIES

    def test_binance_exclusive_no_second_declarer(self):
        """三键必须**只有 binance 声明**——非 bar 项被 ≥2 源声称会让前端下拉误导（切过去即炸）。"""
        from src.data_platform.adapters.base import _ADAPTERS
        for sid in ("binance_perp_hourly", "binance_perp_1min", "binance_perp_15min"):
            owners = [p for p, c in _ADAPTERS.items() if sid in set(c.capabilities)]
            assert owners == ["binance"], (sid, owners)


# ---------------------------------------------------------------------------
# 3：窗口与首跑天数
# ---------------------------------------------------------------------------


class TestCryptoWindow:
    def test_first_run_window_is_exactly_n_days(self):
        from src.data_sync.engine import _crypto_window
        # end_date 取过去日，避开 UTC 昨日夹取
        s, e = _crypto_window({"id": "x", "last_sync_date": None}, "20260131", None, 7)
        assert e == date(2026, 1, 31)
        assert s == date(2026, 1, 25), "首跑窗口＝7 天（含端点）"

    def test_incremental_start_is_cursor_plus_one(self):
        from src.data_sync.engine import _crypto_window
        s, _e = _crypto_window({"id": "x", "last_sync_date": "20260110"}, "20260131", None, 7)
        assert s == date(2026, 1, 11), "有游标时**不用**首跑窗口，逐日续"

    def test_backfill_from_wins(self):
        from src.data_sync.engine import _crypto_window
        s, _e = _crypto_window({"id": "x", "last_sync_date": "20260110"}, "20260131",
                               "20250601", 7)
        assert s == date(2025, 6, 1)

    def test_end_clamped_to_utc_yesterday(self):
        from src.data_sync.engine import _crypto_window
        today = datetime.now(timezone.utc).date()
        _s, e = _crypto_window({"id": "x"}, (today + timedelta(days=3)).strftime("%Y%m%d"),
                               None, 1)
        assert e == today - timedelta(days=1), "上界不得是 UTC 今日（当日文件必 404）"


# ---------------------------------------------------------------------------
# 4：handler 工厂（离线桩）
# ---------------------------------------------------------------------------


class _FakeBinance:
    """最小供给面替身：记录调用 + 按给定日造行。"""

    def __init__(self, symbols=("BTCUSDT",), day: date | None = None, empty=False, boom=False):
        self._symbols = list(symbols)
        self._day = day
        self._empty = empty
        self._boom = boom
        self.calls: list[dict] = []

    def list_symbols(self, refresh=False):
        return list(self._symbols)

    def fetch_supply(self, kind, sub_kind=None, **p):
        self.calls.append({"kind": kind, "sub_kind": sub_kind, **p})
        if self._boom:
            raise RuntimeError("upstream down")
        if self._empty:
            return pd.DataFrame()
        return pd.DataFrame([{"binance_symbol": p["symbol"], "open_time": 0}])

    def to_bar_rows(self, df, freq, adj_map=None):
        if df is None or df.empty:
            return []
        sym = str(df["binance_symbol"].iloc[0])
        ts = datetime(self._day.year, self._day.month, self._day.day, tzinfo=timezone.utc)
        return [(f"{sym}.BINANCE", freq, ts, 1.0, 2.0, 0.5, 1.5, 10.0, 20.0, None, "binance")]


def _run(sync_id, adapter, cfg=None, end_date="20260131", backfill=None):
    from src.data_sync import engine
    cfg = cfg or {"id": sync_id, "provider": "binance", "last_sync_date": None}
    with patch.object(engine, "_get_supply_adapter", return_value=adapter), \
         patch("src.data_platform.db.save_bars", return_value=1) as sb, \
         patch("src.data_platform.db.save_bars_overwrite", return_value=1) as so:
        r = engine._HANDLERS[sync_id](cfg, end_date, backfill)
    return r, sb, so


class TestHandlerFactory:
    def test_freq_and_table_follow_spec(self):
        """落库 freq 必须是该 sync_id 的 spec freq（`bar_{freq.lower()}` 才能对上）。"""
        ad = _FakeBinance(day=date(2026, 1, 20))
        _r, sb, _so = _run("binance_perp_hourly", ad)
        assert sb.call_args[0][0] == "1h", sb.call_args
        ad = _FakeBinance(day=date(2026, 1, 20))
        _r, sb, _so = _run("binance_perp_15min", ad)
        assert sb.call_args[0][0] == "15min", sb.call_args

    def test_fetch_uses_kind_subkind_and_freq(self):
        ad = _FakeBinance(day=date(2026, 1, 20))
        _run("binance_perp_1min", ad)
        c = ad.calls[0]
        assert (c["kind"], c["sub_kind"], c["freq"]) == ("bar_minute", "perp", "1min")
        # 1min 首跑窗口＝1 天（spec 的 first_run_days；存储闸门），故 start == end
        assert c["start"] == "20260131" and c["end"] == "20260131"

    def test_cursor_is_last_day_with_data_not_window_end(self):
        """⭐ 本批修的真缺陷：游标落在**真取到数据的那一日**。

        窗口 [01-25, 01-31]，上游只给到 01-29 ⇒ 游标必须是 20260129。
        按窗口上界（20260131）推 ⇒ 01-30/01-31 永不重试（静默数据洞）。
        """
        ad = _FakeBinance(day=date(2026, 1, 29))
        r, _sb, _so = _run("binance_perp_hourly", ad)
        assert r["cursor_upto"] == "20260129", r
        assert r["failed_dates"] == []

    def test_cursor_max_across_symbols(self):
        """多标的各行日期不同 ⇒ 取**最大**（最后有数据的一日）。"""
        from src.data_sync import engine

        class _Multi(_FakeBinance):
            def to_bar_rows(self, df, freq, adj_map=None):
                sym = str(df["binance_symbol"].iloc[0])
                d = {"A": 27, "B": 29, "C": 28}[sym]
                return [(f"{sym}.BINANCE", freq,
                         datetime(2026, 1, d, tzinfo=timezone.utc),
                         1.0, 2.0, 0.5, 1.5, 1.0, 1.0, None, "binance")]

        ad = _Multi(symbols=("A", "B", "C"))
        with patch.object(engine, "_get_supply_adapter", return_value=ad), \
             patch("src.data_platform.db.save_bars", return_value=1):
            r = engine._HANDLERS["binance_perp_hourly"](
                {"id": "binance_perp_hourly", "provider": "binance", "last_sync_date": None},
                "20260131", None)
        assert r["cursor_upto"] == "20260129", r

    def test_unpublished_tail_is_skipped_not_failed(self):
        """整窗都在「未发布尾区」⇒ 0 行是**正常等待**：log_status=skipped、无 failed 项。"""
        from src.data_sync import engine
        today = datetime.now(timezone.utc).date()
        ad = _FakeBinance(empty=True)
        cfg = {"id": "binance_perp_1min", "provider": "binance", "last_sync_date": None}
        end = (today - timedelta(days=1)).strftime("%Y%m%d")
        with patch.object(engine, "_get_supply_adapter", return_value=ad), \
             patch("src.data_platform.db.save_bars", return_value=0):
            r = engine._HANDLERS["binance_perp_1min"](cfg, end, None)
        assert r["pulled"] == 0
        assert r["failed_dates"] == [], r
        assert r["log_status"] == "skipped"

    def test_long_window_zero_rows_is_visible(self):
        """窗口足够长却 0 行＝真异常（上游不可达/路径变更）——必须进 failed。"""
        ad = _FakeBinance(empty=True)
        r, _sb, _so = _run("binance_perp_hourly", ad)
        assert any(x.startswith("no_rows:") for x in r["failed_dates"]), r
        assert "log_status" not in r

    def test_per_symbol_exception_does_not_abort(self):
        ad = _FakeBinance(symbols=("A", "B"), day=date(2026, 1, 29))
        calls = {"n": 0}
        orig = ad.fetch_supply

        def _flaky(kind, sub_kind=None, **p):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("hiccup")
            return orig(kind, sub_kind, **p)

        ad.fetch_supply = _flaky
        r, _sb, _so = _run("binance_perp_hourly", ad)
        assert r["pulled"] == 1 and any(x.startswith("A:") for x in r["failed_dates"]), r
        assert r["cursor_upto"] == "20260129", "B 成功 ⇒ 游标仍推进到 01-29"

    def test_partial_advance_uses_last_successful_day(self):
        """全部失败：游标退回「起点前一日」＝**旧游标值**（绝不空转、绝不跳日）。"""
        ad = _FakeBinance(empty=False, boom=True)
        r, _sb, _so = _run("binance_perp_hourly", ad)
        assert r["cursor_upto"] == "20260124", r        # start 20260125 - 1
        assert r["pulled"] == 0

    def test_backfill_uses_overwrite(self):
        ad = _FakeBinance(day=date(2026, 1, 20))
        _r, sb, so = _run("binance_perp_1min", ad,
                          cfg={"id": "binance_perp_1min", "provider": "binance",
                               "last_sync_date": "20260110"},
                          backfill="20260119")
        assert so.call_count == 1 and sb.call_count == 0, "回补必须 overwrite（手动优先级最高）"

    def test_empty_symbol_list_raises_loud(self):
        from src.data_sync import engine
        ad = _FakeBinance(symbols=())
        with patch.object(engine, "_get_supply_adapter", return_value=ad):
            with pytest.raises(RuntimeError):
                engine._HANDLERS["binance_perp_hourly"](
                    {"id": "binance_perp_hourly", "provider": "binance"}, "20260131", None)

    def test_start_after_end_is_noop(self):
        from src.data_sync import engine
        ad = _FakeBinance(day=date(2026, 1, 20))
        with patch.object(engine, "_get_supply_adapter", return_value=ad), \
             patch("src.data_platform.db.save_bars", return_value=1):
            r = engine._HANDLERS["binance_perp_1min"](
                {"id": "binance_perp_1min", "provider": "binance",
                 "last_sync_date": "20260131"}, "20260131", None)
        assert r["pulled"] == 0 and r["cursor_upto"] == "20260131"
        assert ad.calls == [], "窗口压空时不得发起任何拉取"

    def test_progress_cb_called_per_symbol(self):
        from src.data_sync import engine
        ad = _FakeBinance(symbols=("A", "B", "C"), day=date(2026, 1, 29))
        seen: list[tuple] = []
        with patch.object(engine, "_get_supply_adapter", return_value=ad), \
             patch("src.data_platform.db.save_bars", return_value=1):
            engine._HANDLERS["binance_perp_hourly"](
                {"id": "binance_perp_hourly", "provider": "binance", "last_sync_date": None},
                "20260131", None, progress_cb=lambda i, n, s: seen.append((i, n, s)))
        assert seen == [(1, 3, "A"), (2, 3, "B"), (3, 3, "C")]


# ---------------------------------------------------------------------------
# 5：真库对账（配置面 ↔ 代码声明）
# ---------------------------------------------------------------------------


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
class TestDbReconciliation:
    NEW = ("binance_perp_hourly", "binance_perp_1min", "binance_perp_15min")

    def _rows(self, sql, params=None):
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            return conn.execute(sql, params).fetchall()

    def test_sync_config_rows_match_spec(self):
        from src.data_sync.engine import _BINANCE_BAR_SPECS
        got = {r[0]: r for r in self._rows(
            "SELECT id, provider, pg_table, data_type, sync_mode, trade_day_filter, "
            "supports_backfill, enabled, schedule, start_floor FROM sync_config "
            "WHERE id = ANY(%s)", (list(self.NEW),))}
        assert set(got) == set(self.NEW), got
        for sid in self.NEW:
            _kind, _sub, freq, _days = _BINANCE_BAR_SPECS[sid]
            r = got[sid]
            assert r[1] == "binance" and r[3] == "crypto"
            assert r[2] == f"bar_{freq.lower()}", (sid, r[2], freq)
            assert r[4] == "incremental" and r[5] == "none", "crypto＝连续轴，无交易日历"
            assert r[6] is True, "handler 真读 backfill_from ⇒ supports_backfill 必须 true"
            assert r[7] is True, "enabled=true 才允许 UI 手动触发（disabled 会被 sync() 拒）"
            assert r[9] is not None, "start_floor＝回补下限（有界占用闸门）"

    def test_manual_schedule_is_the_storage_gate(self):
        """🔴 `schedule='manual'` 是**存储闸门**，不是笔误。

        日调度＝每天新增一天且只增不删：1min ≈450MB/天、prod 可用 8.3G ⇒ 约 18 天打满磁盘。
        改成 cron 前必须先扩盘（并把 start_floor 一起放宽）——那是运维显式决策。
        """
        rows = dict(self._rows("SELECT id, schedule FROM sync_config WHERE id = ANY(%s)",
                               (list(self.NEW),)))
        assert rows == {sid: "manual" for sid in self.NEW}, rows

    def test_sync_kind_rows_match_spec(self):
        from src.data_sync.engine import _BINANCE_BAR_SPECS
        got = {r[0]: r for r in self._rows(
            "SELECT sync_id, kind, sub_kind, pg_table, rebuild, pk_cols FROM sync_kind_config "
            "WHERE sync_id = ANY(%s)", (list(self.NEW),))}
        assert set(got) == set(self.NEW), got
        for sid in self.NEW:
            kind, sub, freq, _days = _BINANCE_BAR_SPECS[sid]
            r = got[sid]
            assert (r[1], r[2]) == (kind, sub), (sid, r[1], r[2], kind, sub)
            assert r[3] == f"bar_{freq.lower()}"
            assert r[4] == "incremental" and list(r[5]) == ["symbol", "ts"]

    def test_tables_exist(self):
        names = {r[0] for r in self._rows(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename = ANY(%s)",
            (["bar_1h", "bar_1min", "bar_15min"],))}
        assert names == {"bar_1h", "bar_1min", "bar_15min"}, \
            "三表由 0064 迁移建好；缺表＝落库时报 relation does not exist"
