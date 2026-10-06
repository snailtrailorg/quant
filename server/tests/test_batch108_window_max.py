"""批 108·步 3：窗口下界「**三方地板取 max**」＋ §5.3 排除段登记。

设计依据：`flow/方案/同步窗口与参数分层-设计.md` §5.1（四边界）· §5.3（**封顶不得静默**）·
§八.3（实施步）· §十 B/H（裁定）；任务文件 `flow/任务/批108-同步窗口边界模型.md`。

钉的是**行为**（不是「函数存在」）：

1. **三地板取 max** —— `max(inception, retention, source_earliest)`，谁最晚谁绑；
2. **`policy_discard` 登记** —— `retention > inception` ⇒ `[inception, retention)` 是「主动不保留」，
   必须**显式登记**（静默＝无声丢史，且与「源头就没有」同形）；
3. **`unreachable` 登记** —— `source_earliest > inception` ⇒ `[inception, source_earliest)` 是
   「存在但源不可达」，**不得**当作我们的缺口；
4. **F-1「首跑即全史」** —— 无游标时下界＝三地板，**不是** `today − 30d`（假首跑地板已删）；
5. **三源皆空 ⇒ 有界兜底 + 响亮告警**（不静默全史）；
6. **per-date 四站同源** —— `_sync_via_kind` 的硬编码 `20050408` 已删，改由 `retention` 承载。

DB 依赖：无（全离线桩）。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from src.data_sync import engine


class _Stub:
    """可配置桩：三地板的三源各由一个入参控制。"""

    provider = "stub"
    venue = "STUB"

    def __init__(self, src_lo=None, lag=0, inceptions=None):
        self._src_lo = src_lo
        self._lag = lag
        self._inc = {str(k): v for k, v in (inceptions or {}).items()}
        self.calls: list[dict] = []

    def available_range(self, kind):
        return (self._src_lo, None)

    def publish_lag(self, kind):
        return self._lag

    def symbol_inception(self, symbol):
        return self._inc.get(str(symbol))


# ---------------------------------------------------------------------------
# 1：三地板取 max（纯函数，逐源覆盖）
# ---------------------------------------------------------------------------


class TestThreeFloorMax:
    def test_retention_wins_when_latest(self):
        ad = _Stub(src_lo="2019-12-31")
        f, ex = engine._window_floors(ad, "bar_daily",
                                      {"retention": date(2022, 1, 1)}, sym="BTC")
        assert f == date(2022, 1, 1)

    def test_inception_wins_when_latest(self):
        ad = _Stub(src_lo="2019-12-31", inceptions={"BTC": "2021-05-05"})
        f, _ex = engine._window_floors(ad, "bar_daily", {}, sym="BTC")
        assert f == date(2021, 5, 5), "上币日晚于源界 ⇒ 上币日绑（全史里它本来就不存在）"

    def test_source_earliest_wins_when_latest(self):
        """OKX 形状：`listTime=2019-11-12`（上币日）早于 `1Dutc` 实测下界 ⇒ 源界绑。"""
        ad = _Stub(src_lo="2020-01-01", inceptions={"BTC-USDT-SWAP": "2019-11-12"})
        f, _ex = engine._window_floors(ad, "bar_daily", {}, sym="BTC-USDT-SWAP")
        assert f == date(2020, 1, 1)

    def test_source_none_is_not_a_constraint(self):
        """`earliest=None` ＝ 源不构成下界（不是「不知道」）——inception 单独绑。"""
        ad = _Stub(src_lo=None, inceptions={"X": "2015-03-01"})
        f, _ex = engine._window_floors(ad, "index_daily", {}, sym="X")
        assert f == date(2015, 3, 1)

    def test_accepts_iso_and_compact_date_forms(self):
        """源界/inception 来自 ISO 串，`retention` 来自 DB `date` —— 两种形态都要吃得下。"""
        ad = _Stub(src_lo="2019-12-31", inceptions={"A": "2020-02-03"})
        f, _ = engine._window_floors(ad, "bar_daily", {}, sym="A")
        assert f == date(2020, 2, 3), "inception 走 ISO 串"
        assert engine._ymd("20191231") == date(2019, 12, 31)
        assert engine._ymd("2019/12/31") == date(2019, 12, 31)
        assert engine._ymd(date(2019, 12, 31)) == date(2019, 12, 31)

    def test_family_level_ignores_inception(self):
        """per-date 族**没有**逐个标的的生命周期 ⇒ 家族级地板＝`max(retention, 源界)`（§九.7）。"""
        ad = _Stub(src_lo="2019-12-31", inceptions={"BTC": "2025-01-01"})
        f, _ex = engine._window_floors(ad, "bar_daily", {}, sym=None)
        assert f == date(2019, 12, 31), "家族级不取 per-symbol inception"


# ---------------------------------------------------------------------------
# 2：§5.3 排除段登记（封顶不得静默）
# ---------------------------------------------------------------------------


class TestExclusionRegistration:
    def test_policy_discard_when_retention_after_inception(self):
        ad = _Stub(src_lo="2019-12-31", inceptions={"BTC": "2020-06-01"})
        _f, ex = engine._window_floors(ad, "bar_daily",
                                       {"retention": date(2022, 1, 1)}, sym="BTC")
        kinds = {(e["kind"], e["from"], e["to"]) for e in ex}
        assert ("policy_discard", "2020-06-01", "2022-01-01") in kinds, ex

    def test_no_policy_discard_when_retention_not_capping(self):
        ad = _Stub(src_lo="2019-12-31", inceptions={"BTC": "2020-06-01"})
        _f, ex = engine._window_floors(ad, "bar_daily",
                                       {"retention": date(2020, 1, 1)}, sym="BTC")
        assert not [e for e in ex if e["kind"] == "policy_discard"], \
            "retention 早于上币日 ⇒ 未封顶任何存在的历史 ⇒ 不得登记"

    def test_unreachable_when_source_after_inception(self):
        ad = _Stub(src_lo="2020-01-01", inceptions={"SWAP": "2019-11-12"})
        _f, ex = engine._window_floors(ad, "bar_daily", {}, sym="SWAP")
        kinds = {(e["kind"], e["from"], e["to"]) for e in ex}
        assert ("unreachable", "2019-11-12", "2020-01-01") in kinds, ex

    def test_no_unreachable_when_source_earlier(self):
        ad = _Stub(src_lo="2019-12-31", inceptions={"SWAP": "2020-06-01"})
        _f, ex = engine._window_floors(ad, "bar_daily", {}, sym="SWAP")
        assert not [e for e in ex if e["kind"] == "unreachable"]

    def test_inception_unknown_retention_still_registers_policy_discard(self):
        """inception=None（裁定 F：未知）**不得**让 `retention` 封顶变成静默。

        步 4 盲审必修-2：判据用「已知下界＝`max(inception, source_earliest)`」——
        存活实例 `binance_perp_hourly`（retention 2026-09-29 / source 2019-12-31 /
        inception NULL ⇒ 静默弃约 7 年）。
        """
        ad = _Stub(src_lo="2020-01-01")
        _f, ex = engine._window_floors(ad, "bar_daily", {"retention": date(2023, 1, 1)},
                                       sym="UNKNOWN")
        kinds = {(e["kind"], e["from"], e["to"]) for e in ex}
        assert ("policy_discard", "2020-01-01", "2023-01-01") in kinds, ex

    def test_inception_unknown_and_retention_not_capping_no_exclusions(self):
        """inception 未知且 `retention` 早于源界 ⇒ 未封顶任何已知历史 ⇒ 不得登记。"""
        ad = _Stub(src_lo="2020-01-01")
        _f, ex = engine._window_floors(ad, "bar_daily", {"retention": date(2019, 1, 1)},
                                       sym="UNKNOWN")
        assert ex == []

    def test_unavailable_range_still_loud(self):
        """源界是**硬契约**（步 1）：未覆写即响亮抛，不得返回 today 冒充。"""
        from src.data_platform.adapters.base import BaseDataAdapter

        bare = type("_BareNoRange", (BaseDataAdapter,), {
            "provider": "bare",
            "pull_daily": lambda self, *a, **k: pd.DataFrame(),
            "pull_minute": lambda self, *a, **k: pd.DataFrame(),
            "to_bar_rows": lambda self, *a, **k: [],
        })()

        with pytest.raises(NotImplementedError):
            engine._window_floors(bare, "bar_daily", {}, sym=None)


# ---------------------------------------------------------------------------
# 3：家族级起点（per-date 四站共用）与有界兜底
# ---------------------------------------------------------------------------


class TestFamilyStart:
    def test_family_start_is_max_retention_and_source(self):
        f, _ex = engine._family_start(_Stub(src_lo="2010-01-01"),
                                      "fundamental_daily", {"retention": date(1990, 12, 19)})
        assert f == date(2010, 1, 1), "源界晚于 retention ⇒ 源界绑"

        f2, _ = engine._family_start(_Stub(src_lo=None), "index_daily",
                                     {"retention": date(2005, 4, 8)})
        assert f2 == date(2005, 4, 8), "index_daily 源界 None ⇒ retention 绑（§九.7）"

    def test_all_three_absent_falls_back_bounded_and_warns(self, caplog):
        """三源皆空 ⇒ **有界**兜底 + 响亮告警——不得静默退化成「从源头全史」。"""
        with caplog.at_level(logging.WARNING, logger=engine.logger.name):
            f, ex = engine._family_start(_Stub(src_lo=None), "bar_daily", {"id": "s1"})
        assert f == date.today() - timedelta(days=engine._WINDOW_FALLBACK_DAYS)
        assert ex == []
        assert any("窗口下界三源皆未声明" in r.message for r in caplog.records), caplog.text


# ---------------------------------------------------------------------------
# 4：crypto handler 端到端（F-1 首跑即全史 + per-symbol 窗口 + 排除登记可见）
# ---------------------------------------------------------------------------


class _BarStub(_Stub):
    """crypto handler 用的桩：`fetch_supply` 记录 (symbol, start, end)。"""

    def __init__(self, symbols=("BTC",), **kw):
        super().__init__(**kw)
        self._syms = list(symbols)

    def list_symbols(self, refresh=False):
        return list(self._syms)

    def fetch_supply(self, kind, sub_kind=None, **p):
        self.calls.append({"symbol": p["symbol"], "start": p["start"], "end": p["end"]})
        return pd.DataFrame([{"s": p["symbol"]}])

    def to_bar_rows(self, df, freq, adj_map=None):
        ts = datetime(2024, 1, 2, tzinfo=timezone.utc)
        return [(f"{df['s'].iloc[0]}.STUB", freq, ts,
                 1.0, 2.0, 0.5, 1.5, 1.0, 1.0, None, "stub")]


def _run_crypto(adapter, cfg, end="20260131", backfill=None):
    with patch.object(engine, "_get_supply_adapter", return_value=adapter), \
         patch.object(engine, "_sm_upsert_crypto", return_value=None), \
         patch("src.data_platform.db.save_bars", return_value=1), \
         patch("src.data_platform.db.save_bars_overwrite", return_value=1):
        return engine._HANDLERS["binance_perp_daily"](cfg, end, backfill)


class TestCryptoWindowEndToEnd:
    def test_first_run_lower_bound_is_not_today_minus_30(self):
        """⭐ F-1：crypto 族游标从未闭合 ⇒ 首跑下界＝三地板（源界），**不是** `today−30d`。"""
        ad = _BarStub(src_lo="2019-12-31")
        r = _run_crypto(ad, {"id": "binance_perp_daily", "provider": "binance",
                             "last_sync_date": None})
        assert ad.calls[0]["start"] == "20191231", ad.calls
        assert r["start"] == "20191231"

    def test_cursor_still_wins_over_floor(self):
        ad = _BarStub(src_lo="2019-12-31")
        _run_crypto(ad, {"id": "binance_perp_daily", "provider": "binance",
                         "last_sync_date": "20240101"})
        assert ad.calls[0]["start"] == "20240102"

    def test_retention_caps_the_first_run(self):
        ad = _BarStub(src_lo="2019-12-31")
        _run_crypto(ad, {"id": "binance_perp_daily", "provider": "binance",
                         "last_sync_date": None, "retention": date(2026, 1, 25)})
        assert ad.calls[0]["start"] == "20260125"

    def test_per_symbol_window_uses_inception(self):
        """per-symbol 族：每标的 `max(家族起点, inception(sym))`（设计 §5.1「标的×日」）。"""
        ad = _BarStub(symbols=("OLD", "NEW"), src_lo="2019-12-31",
                      inceptions={"NEW": "2025-07-01"})
        _run_crypto(ad, {"id": "binance_perp_daily", "provider": "binance",
                         "last_sync_date": None})
        got = {c["symbol"]: c["start"] for c in ad.calls}
        assert got == {"OLD": "20191231", "NEW": "20250701"}, got

    def test_symbol_after_end_is_skipped_not_failed(self):
        """下界晚于窗口上界的标的（新上币）⇒ 本轮无作业，**不是失败**。"""
        ad = _BarStub(symbols=("OLD", "FUTURE"), src_lo="2019-12-31",
                      inceptions={"FUTURE": "2030-01-01"})
        r = _run_crypto(ad, {"id": "binance_perp_daily", "provider": "binance",
                             "last_sync_date": None})
        assert [c["symbol"] for c in ad.calls] == ["OLD"]
        assert r["failed_dates"] == []

    def test_exclusions_are_registered_and_not_failures(self):
        """排除段进 `excluded`（可见登记），**绝不**进 `failed_dates`（不误告警）。"""
        ad = _BarStub(symbols=("SWAP",), src_lo="2020-01-01",
                      inceptions={"SWAP": "2019-11-12"})
        r = _run_crypto(ad, {"id": "binance_perp_daily", "provider": "binance",
                             "last_sync_date": None, "retention": date(2022, 1, 1)})
        kinds = {e["kind"] for e in r["excluded"]}
        assert kinds == {"unreachable", "policy_discard"}, r["excluded"]
        assert r["failed_dates"] == []

    def test_publish_lag_moves_upper_bound(self):
        """上界＝`today(UTC) − publish_lag`（源属性）——lag=2 比 lag=1 再退一天。"""
        today = datetime.now(timezone.utc).date()
        ad1 = _BarStub(src_lo="2020-01-01", lag=1)
        ad2 = _BarStub(src_lo="2020-01-01", lag=2)
        end = (today + timedelta(days=5)).strftime("%Y%m%d")
        _run_crypto(ad1, {"id": "binance_perp_daily", "provider": "binance",
                          "last_sync_date": None}, end=end)
        _run_crypto(ad2, {"id": "binance_perp_daily", "provider": "binance",
                          "last_sync_date": None}, end=end)
        assert ad1.calls[0]["end"] == (today - timedelta(days=1)).strftime("%Y%m%d")
        assert ad2.calls[0]["end"] == (today - timedelta(days=2)).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# 5：per-date 四站（`_sync_via_kind` 的硬编码 `20050408` 已删）
# ---------------------------------------------------------------------------


class TestPerDateStationFloor:
    def _capture(self, kind, sub, cfg, adapter):
        seen: dict = {}

        def _rec(*a, **k):
            seen["start"] = k.get("start", a[2] if len(a) > 2 else None)
            return {}

        with patch.object(engine, "_read_sync_kind",
                          return_value={"kind": kind, "sub_kind": sub}), \
             patch.object(engine, "_get_kline_adapter", return_value=adapter), \
             patch.object(engine, "_sync_via_kind_daily_batch", side_effect=_rec), \
             patch.object(engine, "_sync_via_kind_cb_daily", side_effect=_rec), \
             patch.object(engine, "_sync_via_kind_index", side_effect=_rec):
            engine._sync_via_kind(cfg, "20260131", None)
        return seen["start"]

    def test_index_daily_retention_replaces_hardcoded_20050408(self):
        """值不变、来源正名：`2005-04-08` 从**硬编码**改成 `retention` 列承载。"""
        s = self._capture("index_daily", None,
                          {"id": "index_daily", "provider": "tushare",
                           "last_sync_date": None, "retention": date(2005, 4, 8)},
                          _Stub(src_lo=None))
        assert s == "20050408"

    def test_bar_daily_uses_source_floor_not_fake_default_days(self):
        """`default_days=30`（假首跑地板）已换掉 ⇒ 无游标时下界＝源界。"""
        s = self._capture("bar_daily", "stock",
                          {"id": "astock_daily", "provider": "tushare",
                           "last_sync_date": None},
                          _Stub(src_lo="2010-01-01"))
        assert s == "20100101"

    def test_backfill_start_clamped_by_floor(self):
        s = self._capture("index_daily", None,
                          {"id": "index_daily", "provider": "tushare",
                           "last_sync_date": None, "retention": date(2005, 4, 8)},
                          _Stub(src_lo=None))
        assert s == "20050408"
        seen: dict = {}

        def _rec(*a, **k):
            seen["start"] = k["start"]
            return {}

        with patch.object(engine, "_read_sync_kind",
                          return_value={"kind": "index_daily", "sub_kind": None}), \
             patch.object(engine, "_get_kline_adapter", return_value=_Stub(src_lo=None)), \
             patch.object(engine, "_sync_via_kind_index", side_effect=_rec):
            engine._sync_via_kind({"id": "index_daily", "provider": "tushare",
                                   "last_sync_date": None,
                                   "retention": date(2005, 4, 8)}, "20260131", "19990101")
        assert seen["start"] == "20050408", "回补起点早于地板 ⇒ 抬到地板（源拿不到更早）"
