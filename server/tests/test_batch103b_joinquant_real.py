"""批 103b · 聚宽（JQData）真接：A 股日线历史切片落 `bar_1d`。

**本文件全部离线**（无网络、无凭证、无真库）：把 2026-10-06 dev 实测出的聚宽「形状/边界/单位」
真值固化成**可执行断言**。真值来源见 `flow/任务/批103b-聚宽真接.md` §〇、`joinquant_adapter.py`
模块 docstring。

按「真故障」分组：

1. **形状分叉**（`TestNormalizeFrame`）——聚宽 `get_price` 单标的返 `DatetimeIndex`（无 code 列）、
   多标的返带 `time`/`code` 列的平面表。统一帧写错＝**列名错位**（静默把 open 当 close）。
2. **符号归一**（`TestSymbolNormalization`）——`XSHE↔SZSE`/`XSHG↔SHSE`（**不是** `.SZ/.SH`）。
3. **落库行**（`TestToBarRows`）——11 字段元组、`adj_factor=None`（跨源基准不同不写）、
   `volume/amount` **不换算**（内部本就是股/元——批 103 注释的 ÷100 是错的）。
4. **调用参数钉**（`TestPullDaily`）——`fq=None`/`panel=False`/`skip_paused=True` 三个必须显式传
   （`fq` 默认 `pre` 会改价＝破坏「未复权价」契约）；分批 ≤800 只/请求。
5. **凭证解析**（`TestDataSourceCredentials`）——DB 密文（JSON `{account,password}`）优先、
   `.env` 兜底；**缺凭证必须抛 `ProviderConfigError`**（响亮，禁静默回落 tushare＝串源）。
6. **handler 契约**（`TestHandler`）——窗口**动态取 + 起点自夹**（SDK 不查 `start_date` ⇒ 不夹＝
   额度炸弹）、行预算、**provider 级互斥**（连接数=1）、游标只推进「已完成日」、空标的响亮 raise、
   写入走 `save_bars_overwrite`。
7. **接线**（`TestWiring`）——`_HANDLERS` 有它、`_VIA_KIND_IDS` **没有**它（非 bar 族静态路由）。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from src.data_platform.adapters import joinquant_adapter as JQ


# ---------------------------------------------------------------------------
# 桩
# ---------------------------------------------------------------------------

class _FakeJQModule:
    """假 jqdatasdk 模块：记录 get_price 入参，返回预置帧。"""

    def __init__(self, frame: pd.DataFrame | None = None):
        self._frame = frame if frame is not None else pd.DataFrame()
        self.calls: list[dict] = []

    def get_price(self, security, **kw):
        self.calls.append({"security": security, **kw})
        return self._frame


def _single_symbol_frame(dates=("2026-06-01", "2026-06-02")) -> pd.DataFrame:
    """单标的形状：DatetimeIndex + 字段列（**无 code 列**）。"""
    idx = pd.to_datetime(list(dates))
    return pd.DataFrame(
        {"open": [10.0, 11.0], "close": [10.5, 11.5], "high": [10.9, 11.9],
         "low": [9.9, 10.9], "volume": [95_459_569, 88_000_000],
         "money": [1_042_306_456, 970_000_000]},
        index=idx,
    )


def _multi_symbol_frame() -> pd.DataFrame:
    """多标的形状：RangeIndex + `time`/`code` 列。"""
    return pd.DataFrame({
        "time": pd.to_datetime(["2026-06-01", "2026-06-01"]),
        "code": ["000001.XSHE", "600000.XSHG"],
        "open": [10.0, 20.0], "close": [10.5, 20.5], "high": [10.9, 20.9],
        "low": [9.9, 19.9], "volume": [100.0, 200.0], "money": [1000.0, 2000.0],
    })


def _multi_symbol_frame_of(jq_codes) -> pd.DataFrame:
    """为任意 jq 代码列表造「多标的形状」帧（每个代码一行）。"""
    codes = list(jq_codes)
    n = len(codes)
    return pd.DataFrame({
        "time": pd.to_datetime(["2026-06-01"] * n),
        "code": codes,
        "open": [1.0] * n, "close": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n,
        "volume": [10.0] * n, "money": [100.0] * n,
    })


class _FakeJQAdapter:
    """handler 用假 adapter（只测编排，不碰真聚宽）。"""

    provider = "joinquant"

    def __init__(self, win_start=(2025, 6, 28), win_end=(2026, 7, 5),
                 spare=1_000_000, total=1_000_000, rows_per_date=2, fail_dates=()):
        self._win = {"start": date(*win_start), "end": date(*win_end),
                     "expire_time": "2027-01-04", "spare": spare, "total": total}
        self._rows_per_date = rows_per_date
        self._fail = set(fail_dates)
        self.calls: list[str] = []

    def account_window(self):
        return dict(self._win)

    def available_range(self, kind):
        """批 108·步 3：聚宽源界＝**账号窗口**（JQData 的绝对区间，非滚动）。"""
        return (self._win["start"].isoformat(), self._win["end"].isoformat())

    def publish_lag(self, kind):
        return 0

    def pull_daily_batch(self, d, kind="astock", symbols=None):
        self.calls.append(d)
        if d in self._fail:
            raise RuntimeError("boom")
        return pd.DataFrame([{"i": i} for i in range(self._rows_per_date)])

    def to_bar_rows(self, df, freq, adj_map=None):
        return [("000001.SZSE", "1D", datetime(2026, 6, 1, tzinfo=timezone.utc),
                 1.0, 2.0, 0.5, 1.5, 100.0, 200.0, None, "joinquant")
                for _ in range(len(df))]


class _Lock:
    """假 SyncLock（acquired 可控）。"""

    def __init__(self, acquired=True):
        self.acquired = acquired
        self.key = ""

    def __call__(self, key, ttl=None):
        self.key = key
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---------------------------------------------------------------------------
# 1：形状分叉
# ---------------------------------------------------------------------------

class TestNormalizeFrame:
    def test_single_symbol_shape_uses_arg_code(self):
        """单标的（DatetimeIndex、无 code 列）→ ts_code 取自入参。"""
        out = JQ._normalize_frame(_single_symbol_frame(), "000001.XSHE")
        assert list(out.columns) == ["ts_code", "trade_date", "open", "high", "low",
                                     "close", "vol", "amount"]
        assert list(out["ts_code"]) == ["000001.SZ", "000001.SZ"]
        assert list(out["trade_date"]) == ["20260601", "20260602"]   # YYYYMMDD 字符串
        assert list(out["open"]) == [10.0, 11.0]

    def test_multi_symbol_shape_reads_code_column(self):
        out = JQ._normalize_frame(_multi_symbol_frame(), "")
        assert list(out["ts_code"]) == ["000001.SZ", "600000.SH"]
        assert list(out["trade_date"]) == ["20260601", "20260601"]
        assert list(out["vol"]) == [100.0, 200.0]

    def test_empty_frame_returns_empty(self):
        assert JQ._normalize_frame(None, "000001.XSHE").empty
        assert JQ._normalize_frame(pd.DataFrame(), "000001.XSHE").empty

    def test_drops_rows_without_ohlc(self):
        """停牌行（OHLC 缺失）不得进落库行——否则写 0 价污染 bar_1d。"""
        df = _single_symbol_frame()
        df.loc[df.index[0], "close"] = None
        out = JQ._normalize_frame(df, "000001.XSHE")
        assert len(out) == 1 and out.iloc[0]["trade_date"] == "20260602"


# ---------------------------------------------------------------------------
# 2：符号归一
# ---------------------------------------------------------------------------

class TestSymbolNormalization:
    def test_to_source_symbol(self):
        a = JQ.JoinQuantAdapter()
        assert a.to_source_symbol("000001.SZSE") == "000001.XSHE"
        assert a.to_source_symbol("600000.SHSE") == "600000.XSHG"
        assert a.to_source_symbol("000001.SZ") == "000001.XSHE"
        assert a.to_source_symbol("600000.SH") == "600000.XSHG"
        assert a.to_source_symbol("") == "" and a.to_source_symbol("000001") == "000001"

    def test_to_internal_ts_code(self):
        assert JQ.JoinQuantAdapter.to_internal_ts_code("000001.XSHE") == "000001.SZ"
        assert JQ.JoinQuantAdapter.to_internal_ts_code("600000.XSHG") == "600000.SH"

    def test_norm_date_forms(self):
        assert JQ._norm_date("20260601") == "2026-06-01"
        assert JQ._norm_date("2026-06-01") == "2026-06-01"
        assert JQ._norm_date(date(2026, 6, 1)) == "2026-06-01"


# ---------------------------------------------------------------------------
# 3：落库行
# ---------------------------------------------------------------------------

class TestToBarRows:
    def test_eleven_fields_with_none_adj_factor(self):
        """11 字段；`adj_factor` **恒 None**；`volume/amount` **不换算**。"""
        a = JQ.JoinQuantAdapter()
        norm = JQ._normalize_frame(_single_symbol_frame(), "000001.XSHE")
        rows = a.to_bar_rows(norm, "1D")
        assert len(rows) == 2
        r = rows[0]
        assert len(r) == 11
        assert r[0] == "000001.SZSE"          # vt_symbol（内部键，与 tushare 行同键）
        assert r[1] == "1D"
        assert r[7] == 95_459_569.0           # volume＝股，与 bar_1d 同单位（**不 ÷100**）
        assert r[8] == 1_042_306_456.0        # amount＝元
        assert r[9] is None, "复权因子跨源基准不同 ⇒ 必须写 None（由 COALESCE + backfill 兜）"
        assert r[10] == "joinquant"

    def test_empty_df_yields_no_rows(self):
        assert JQ.JoinQuantAdapter().to_bar_rows(pd.DataFrame(), "1D") == []
        assert JQ.JoinQuantAdapter().to_bar_rows(None, "1D") == []


# ---------------------------------------------------------------------------
# 4：调用参数钉
# ---------------------------------------------------------------------------

class TestPullDaily:
    def _adapter_with(self, fake):
        a = JQ.JoinQuantAdapter()
        a.get_client = lambda: fake          # 绕过 auth 门
        return a

    def test_pins_fq_none_panel_false_skip_paused(self):
        """三个参数必须显式传——`fq` 默认 `pre` 会改价（破坏未复权契约）。"""
        fake = _FakeJQModule(_single_symbol_frame())
        a = self._adapter_with(fake)
        df = a.pull_daily("000001.SZSE", "20260601", "20260602")
        assert len(fake.calls) == 1
        c = fake.calls[0]
        assert c["fq"] is None, "fq 必须 pin 成 None（否则拿到前复权价）"
        assert c["panel"] is False, "panel=True 是已废弃 Panel 返回"
        assert c["skip_paused"] is True
        assert c["frequency"] == "daily"
        assert set(c["fields"]) >= {"open", "close", "high", "low", "volume", "money"}
        assert c["start_date"] == "2026-06-01" and c["end_date"] == "2026-06-02"
        assert c["security"] == ["000001.XSHE"]
        assert not df.empty and list(df["ts_code"]) == ["000001.SZ", "000001.SZ"]

    def test_batches_by_800(self):
        fake = _FakeJQModule(pd.DataFrame())
        a = self._adapter_with(fake)
        syms = [f"{i:06d}.SZSE" for i in range(1700)]
        a.pull_daily("", "20260601", "20260601", symbols=syms)
        sizes = [len(c["security"]) for c in fake.calls]
        assert sizes == [800, 800, 100], sizes

    def test_single_batch_failure_bisected_not_dropped(self):
        """单批异常**二分降级**：一只坏标的不得连带丢掉其余 799 只。

        反证：退回「整批 try/except + continue」⇒ 本用例的 800 只全丢（2026-10-06 实测：
        含 `920000.BJ` 的 772 只批次全灭，仅一行 warning ＝静默数据洞）。
        """
        fake = _FakeJQModule(_single_symbol_frame())

        def _flaky(security, **kw):
            fake.calls.append({"security": list(security), **kw})
            if len(fake.calls) == 1:
                raise RuntimeError("upstream hiccup")
            return _multi_symbol_frame_of(security)

        fake.get_price = _flaky
        a = self._adapter_with(fake)
        syms = [f"{i:06d}.SZSE" for i in range(900)]     # 2 批（800 + 100）
        out = a.pull_daily("", "20260601", "20260601", symbols=syms)
        assert len(fake.calls) > 2, "首批发须被二分（否则整批丢）"
        assert len(out) == 900, f"二分后 900 只应全部投递，实得 {len(out)}"

    def test_unsupported_exchange_excluded_before_request(self):
        """聚宽不覆盖的交易所（北交所 `.BJ`）**在发请求前剔除**——否则 jqdatasdk 整批拒。"""
        fake = _FakeJQModule(_single_symbol_frame())
        a = self._adapter_with(fake)
        a.pull_daily("", "20260601", "20260601",
                     symbols=["000001.SZSE", "920000.BJ", "600000.SHSE"])
        assert len(fake.calls) == 1
        assert fake.calls[0]["security"] == ["000001.XSHE", "600000.XSHG"], fake.calls[0]["security"]
        assert a.last_skipped == ["920000.BJ"], a.last_skipped

    def test_all_unsupported_returns_empty_without_request(self):
        fake = _FakeJQModule(_single_symbol_frame())
        a = self._adapter_with(fake)
        out = a.pull_daily("", "20260601", "20260601", symbols=["920000.BJ"])
        assert out.empty and fake.calls == [] and a.last_skipped == ["920000.BJ"]

    def test_isolate_single_bad_symbol_inside_batch(self):
        """批内单只无效标的被逐只隔离，其余照投递。"""
        bad = "000002.XSHE"

        class _RejectBad:
            def __init__(self):
                self.calls = []

            def get_price(self, security, **kw):
                self.calls.append(list(security))
                if bad in security:
                    raise RuntimeError(f"无效的证券代码 '{bad}'")
                return _multi_symbol_frame_of(security)

        fake = _RejectBad()
        a = self._adapter_with(fake)
        syms = ["000001.SZSE", "000002.SZSE", "600000.SHSE", "600519.SHSE"]
        out = a.pull_daily("", "20260601", "20260601", symbols=syms)
        assert a.last_skipped == [bad], a.last_skipped
        assert len(out) == 3, f"4 只中 1 只坏 ⇒ 其余 3 只照投递，实得 {len(out)}"
        assert bad not in set(out["ts_code"])

    def test_pull_daily_batch_requires_symbols(self):
        from src.data_platform.adapters.base import UnsupportedFeature
        with pytest.raises(UnsupportedFeature):
            JQ.JoinQuantAdapter().pull_daily_batch("20260601", "astock", symbols=None)

    def test_pull_daily_batch_rejects_non_astock(self):
        from src.data_platform.adapters.base import UnsupportedFeature
        with pytest.raises(UnsupportedFeature):
            JQ.JoinQuantAdapter().pull_daily_batch("20260601", "etf", symbols=["000001.SZSE"])

    def test_pull_minute_unsupported(self):
        from src.data_platform.adapters.base import UnsupportedFeature
        with pytest.raises(UnsupportedFeature):
            JQ.JoinQuantAdapter().pull_minute("000001.SZSE", "1m", "20260601", "20260602")


# ---------------------------------------------------------------------------
# 5：凭证解析（DataSource）
# ---------------------------------------------------------------------------

class TestDataSourceCredentials:
    def test_json_credentials_decrypted(self):
        from src.quant_common.crypto import encrypt
        from src.data_platform.data_source import JoinQuantDataSource
        enc = encrypt(json.dumps({"account": "13500000000", "password": "pw"}))
        assert JoinQuantDataSource(credentials_encrypted=enc)._get_credentials() == \
            ("13500000000", "pw")

    def test_colon_form_tolerated(self):
        from src.quant_common.crypto import encrypt
        from src.data_platform.data_source import JoinQuantDataSource
        enc = encrypt("acct:secret")
        assert JoinQuantDataSource(credentials_encrypted=enc)._get_credentials() == \
            ("acct", "secret")

    def test_env_fallback(self, monkeypatch):
        from src.data_platform.data_source import JoinQuantDataSource
        monkeypatch.setenv("JQDATA_USER", "envuser")
        monkeypatch.setenv("JQDATA_PASSWORD", "envpw")
        assert JoinQuantDataSource()._get_credentials() == ("envuser", "envpw")

    def test_missing_credentials_raises_loud(self, monkeypatch):
        """缺凭证必须**响亮**抛 ProviderConfigError——静默回落 tushare＝串源（额度/限速记错账）。"""
        from src.data_platform.data_source import JoinQuantDataSource
        from src.quant_common.contract import ProviderConfigError
        monkeypatch.delenv("JQDATA_USER", raising=False)
        monkeypatch.delenv("JQDATA_PASSWORD", raising=False)
        with pytest.raises(ProviderConfigError):
            JoinQuantDataSource().get_client()

    def test_window_is_dynamic_from_account_info(self):
        """窗口取自 `get_account_info()` 真值（**禁硬编码**——续期/到期会变）。"""
        from src.data_platform.data_source import JoinQuantDataSource

        class _C:
            def get_account_info(self):
                return {"date_range_start": "2025-06-28 00:00:00",
                        "date_range_end": "2026-07-05 00:00:00",
                        "expire_time": "2027-01-04"}

            def get_query_count(self):
                return {"total": 1_000_000, "spare": 987_654}

        ds = JoinQuantDataSource()
        ds.get_client = lambda: _C()
        win = ds.account_window()
        assert win["start"] == date(2025, 6, 28) and win["end"] == date(2026, 7, 5)
        assert win["spare"] == 987_654 and win["total"] == 1_000_000

    def test_row_quota_not_interval_model(self):
        """额度按**返回行数**计 ⇒ 间隔制限速表必须为空（填了＝把行额度当调用数，语义错）。"""
        from src.data_platform.data_source import JoinQuantDataSource
        assert JoinQuantDataSource.DEFAULT_RATE_LIMITS == {}


# ---------------------------------------------------------------------------
# 6：handler 契约
# ---------------------------------------------------------------------------

class TestHandler:
    def _run(self, adapter, cfg=None, end="20260603", backfill=None,
             dates=("20260601", "20260602", "20260603"), symbols=("000001.SZSE",),
             acquired=True, saved=2):
        from src.data_sync import engine
        from src.data_sync import sync_lock as sl
        cfg = cfg or {"id": "astock_daily_jq", "provider": "joinquant", "last_sync_date": None}
        lock = _Lock(acquired)
        ranges: list[tuple[str, str]] = []

        def _range(s, e):
            ranges.append((s, e))
            return list(dates) if dates is not None else None

        with patch.object(engine, "_get_kline_adapter", return_value=adapter), \
             patch.object(engine, "_list_static_ts_codes", return_value=list(symbols)), \
             patch.object(engine, "_trade_dates_in_range", side_effect=_range), \
             patch.object(engine, "_local_dates",
                          return_value=set(dates) if dates else set()), \
             patch.object(sl, "SyncLock", lock), \
             patch("src.data_platform.db.save_bars_overwrite", return_value=saved) as so:
            r = engine._sync_astock_daily_jq(cfg, end, backfill)
        return r, so, adapter, lock, ranges

    def test_start_clamped_to_window_start(self):
        """回补起点早于窗口 ⇒ **自夹**到窗口起（SDK 只查 end，不夹 start＝静默拉全史＝额度炸弹）。"""
        ad = _FakeJQAdapter(win_start=(2025, 6, 28))
        r, _so, ad, _lk, rng = self._run(ad, end="20260603", backfill="20200101",
                                         dates=("20250628", "20250630"))
        assert r["start"] == "20250628", "start 必须被夹到窗口起"
        assert rng == [("20250628", "20260603")], rng
        assert ad.calls[0] == "20250628", ad.calls

    def test_end_clamped_to_window_end(self):
        """请求上界超过窗口止 ⇒ 夹到窗口止（否则 SDK 直接拒，整轮白跑）。"""
        ad = _FakeJQAdapter(win_start=(2025, 6, 28), win_end=(2026, 7, 5))
        _r, _so, ad, _lk, rng = self._run(ad, end="20991231", dates=("20260703", "20260705"))
        assert rng == [("20250628", "20260705")], rng
        assert ad.calls == ["20260703", "20260705"], ad.calls

    def test_incremental_start_is_cursor_plus_one(self):
        ad = _FakeJQAdapter()
        r, _so, ad, _lk, _rng = self._run(
            ad, cfg={"id": "astock_daily_jq", "provider": "joinquant",
                     "last_sync_date": "20260601"}, dates=("20260602",))
        assert r["start"] == "20260602" and ad.calls == ["20260602"]

    def test_cursor_upto_is_last_completed_day(self):
        """游标＝**已完成**的最后交易日（不跳日）；中间失败日不推进过它。"""
        ad = _FakeJQAdapter(fail_dates=("20260602",))
        r, _so, _ad, _lk, _rng = self._run(ad, dates=("20260601", "20260602", "20260603"))
        assert r["cursor_upto"] == "20260603", r
        assert any(x.startswith("20260602:") for x in r["failed_dates"]), r["failed_dates"]

    def test_all_days_failed_cursor_falls_back_to_start_minus_one(self):
        ad = _FakeJQAdapter(fail_dates=("20260601", "20260602", "20260603"))
        r, _so, _ad, _lk, _rng = self._run(
            ad, dates=("20260601", "20260602", "20260603"),
            cfg={"id": "astock_daily_jq", "provider": "joinquant",
                 "last_sync_date": "20260531"})
        assert r["cursor_upto"] == "20260531", "全失败 ⇒ 退回起点前一日（下轮重试，绝不跳日）"
        assert len(r["failed_dates"]) == 3

    def test_provider_mutex_skip_does_not_advance_cursor(self):
        """连接数=1：拿不到 provider 锁 ⇒ skipped 且**不推进**游标（保守回退到起点前一日）。"""
        ad = _FakeJQAdapter()
        r, _so, ad, lk, _rng = self._run(
            ad, acquired=False,
            cfg={"id": "astock_daily_jq", "provider": "joinquant",
                 "last_sync_date": "20260601"}, dates=("20260602",))
        assert r["log_status"] == "skipped" and r["pulled"] == 0
        assert r["cursor_upto"] == "20260601"
        assert lk.key == "provider:joinquant", "必须是 provider 级键（per-sync_id 锁挡不住同刻 auth）"
        assert ad.calls == [], "锁未得时不得发起任何拉取"

    def test_budget_exhausted_skips(self):
        ad = _FakeJQAdapter(spare=10_000)     # < _JQ_ROW_RESERVE
        r, _so, ad, _lk, _rng = self._run(
            ad, dates=("20260601",),
            cfg={"id": "astock_daily_jq", "provider": "joinquant",
                 "last_sync_date": "20260601"})
        assert r["log_status"] == "skipped" and r["cursor_upto"] == "20260601"
        assert ad.calls == []

    def test_row_budget_stops_early(self):
        """行预算＝spare − 余量；累计近预算即止（额度按返回行数计）。"""
        from src.data_sync import engine
        ad = _FakeJQAdapter(spare=engine._JQ_ROW_RESERVE + 3, rows_per_date=2)
        r, _so, ad, _lk, _rng = self._run(ad, dates=("20260601", "20260602", "20260603"))
        assert len(ad.calls) == 2, f"预算 3 行、每日 2 行 ⇒ 拉 2 天后停：{ad.calls}"
        assert r["pulled"] == 4

    def test_empty_symbols_raises_loud(self):
        """在册标的为空＝上游静态表未同步 ⇒ 响亮 raise（静默 0 行会被记 success 并推游标）。"""
        from src.data_sync import engine
        ad = _FakeJQAdapter()
        with patch.object(engine, "_get_kline_adapter", return_value=ad), \
             patch.object(engine, "_list_static_ts_codes", return_value=[]), \
             patch.object(engine, "_trade_dates_in_range", return_value=["20260601"]), \
             patch("src.data_sync.sync_lock.SyncLock", _Lock(True)):
            with pytest.raises(RuntimeError):
                engine._sync_astock_daily_jq(
                    {"id": "astock_daily_jq", "provider": "joinquant"}, "20260601", None)

    def test_zero_rows_is_visible(self):
        ad = _FakeJQAdapter(rows_per_date=0)
        r, _so, _ad, _lk, _rng = self._run(ad, dates=("20260601", "20260602"))
        assert r["pulled"] == 0
        assert any(x.startswith("no_rows:") for x in r["failed_dates"]), r["failed_dates"]

    def test_start_after_end_is_skipped_noop(self):
        """窗口压空（游标已到窗口止）⇒ 显式 skipped、不拉、cursor 稳定不 churn。"""
        ad = _FakeJQAdapter(win_end=(2026, 7, 5))
        r, _so, ad, _lk, _rng = self._run(
            ad, cfg={"id": "astock_daily_jq", "provider": "joinquant",
                     "last_sync_date": "20260705"}, end="20260705", dates=("20260706",))
        assert r["log_status"] == "skipped" and r["pulled"] == 0
        assert r["cursor_upto"] == "20260705" and ad.calls == []

    def test_writes_via_overwrite_and_rows_carry_none_factor(self):
        """写入必须走 `save_bars_overwrite`（互写优先级），且行内 `adj_factor is None`。"""
        ad = _FakeJQAdapter()
        r, so, _ad, _lk, _rng = self._run(ad, dates=("20260601",))
        assert so.call_count == 1
        freq, rows = so.call_args[0]
        assert freq == "1D" and rows and all(x[9] is None for x in rows)
        assert r["saved"] == 2

    def test_calendar_uncovered_falls_back_to_calendar_days(self):
        """日历未覆盖 ⇒ fail-open 逐自然日（宁多打不漏拉）。"""
        ad = _FakeJQAdapter()
        _r, _so, ad, _lk, _rng = self._run(ad, end="20260603", backfill="20260601", dates=None)
        assert ad.calls == ["20260601", "20260602", "20260603"], ad.calls

    def test_progress_cb_called(self):
        from src.data_sync import engine
        ad = _FakeJQAdapter()
        seen = []
        with patch.object(engine, "_get_kline_adapter", return_value=ad), \
             patch.object(engine, "_list_static_ts_codes", return_value=["000001.SZSE"]), \
             patch.object(engine, "_trade_dates_in_range", return_value=["20260601", "20260602"]), \
             patch("src.data_sync.sync_lock.SyncLock", _Lock(True)), \
             patch("src.data_platform.db.save_bars_overwrite", return_value=1):
            engine._sync_astock_daily_jq(
                {"id": "astock_daily_jq", "provider": "joinquant"}, "20260602",
                backfill_from="20260601", progress_cb=lambda i, n, d: seen.append((i, n, d)))
        assert seen == [(1, 2, "20260601"), (2, 2, "20260602")]


# ---------------------------------------------------------------------------
# 7：接线（注册表 / 路由 / 不占切换位）
# ---------------------------------------------------------------------------

class TestWiring:
    def test_handler_registered(self):
        from src.data_sync import engine
        assert engine._HANDLERS["astock_daily_jq"] is engine._sync_astock_daily_jq

    def test_not_in_via_kind_ids(self):
        """非 bar 族静态路由（逐日 `_sync_via_kind`）不得含它——它是自有窗口/自有分片的 handler。"""
        from src.data_sync import engine
        assert "astock_daily_jq" not in engine._VIA_KIND_IDS

    def test_adapter_registered(self):
        from src.data_platform.adapters.base import _ADAPTERS, get_adapter
        assert "joinquant" in _ADAPTERS and _ADAPTERS["joinquant"] is JQ.JoinQuantAdapter
        assert get_adapter("joinquant").provider == "joinquant"

    def test_data_source_registered(self):
        from src.data_platform.data_source import _REGISTRY
        assert _REGISTRY.get("joinquant") is not None, "缺 DataSource 注册 ⇒ get_data_source 抛错"

    def test_capability_is_sync_id_level_exclusive(self):
        """声明的是**独立** sync_id（不占 `astock_daily`/`astock_list` 切换位）。

        批 107 起增 `astock_list`（聚宽 `get_all_securities` 供给 A 股清单，与 tushare 同 sync_id
        ⇒ 前者成为 `astock_list` 的**第二 provider**，切换位由此合法开启）。
        """
        caps = set(JQ.JoinQuantAdapter.capabilities)
        assert caps == {"astock_daily_jq", "astock_list"}
        assert "astock_daily" not in caps, "占位会把 astock_daily 判成「可切聚宽」⇒ 最近 3 月数据倒退"

    def test_capability_decls_omit_adj_factor(self):
        """不声明复权因子能力（跨源基准不同；由 tushare 独有回填通道补齐）——零代码改动的落点。"""
        kinds = {(d.kind, d.temporality) for d in JQ.JoinQuantAdapter.capability_decls}
        assert ("bar_daily", "historical") in kinds
        assert ("adj_factor", "historical") not in kinds
