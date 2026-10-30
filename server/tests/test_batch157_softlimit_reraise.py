"""批 157 步 1：crypto 拉取主循环的软时限窄捕获直抛。

防回退锚（10-30 prod 两证）：binance hourly 31/9 天窗两次死于 hard 4200 SIGKILL
而非 soft 3600——per-symbol except Exception 吞掉 SoftTimeLimitExceeded，时限对
crypto 档整体哑火（窄捕获此前只在 _repull_symbol :2494 存在）。

锁三件事：首轮直抛 / 二轮补拉直抛 / 普通业务异常仍走 failed 记账。
离线默认：桩 adapter 走 _make_crypto_bar_handler 内层（save_bars/_sm_upsert 均桩）。
"""
from __future__ import annotations
from datetime import date

import pandas as pd
import pytest

import src.data_sync.engine as E
import src.data_platform.db as DB


class _FakeSoft(Exception):
    """替身——纯测试环境 engine._CelerySoftTimeLimit 为 None，patch 为本类。"""


class _Biz(Exception):
    pass


class _Ad:
    provider = "binance"
    venue = "BINANCE"

    def __init__(self, plan=None):
        self.plan = plan or {}

    def available_range(self, kind):
        return ("2019-12-31", None)

    def publish_lag(self, kind):
        return 1

    def list_symbols(self):
        return ["AAAUSDT", "BBBUSDT", "CCCUSDT"]

    def fetch_supply(self, kind, sub_kind, symbol="", start="", end="", freq=""):
        v = self.plan.get(symbol)
        if isinstance(v, Exception):
            raise v
        return pd.DataFrame([{"ts": pd.Timestamp("2026-10-28", tz="UTC"), "symbol": symbol}])

    def to_bar_rows(self, df, freq):
        # 最小行元组：_pull_one 只用 r[2]（ts，UTC aware）取 reached
        return [(symbol, None, row.ts, None, None, None, None, None, None, None, None, None)
                for symbol, row in zip(df["symbol"], df.itertuples())]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(E, "_CelerySoftTimeLimit", _FakeSoft, raising=False)
    monkeypatch.setattr(E, "_get_supply_adapter", lambda cfg: cfg["_ad"])
    monkeypatch.setattr(E, "_crypto_end", lambda end_date, adapter, kind: date(2026, 10, 28))
    monkeypatch.setattr(E, "_crypto_base_start",
                        lambda cfg, bf, adapter, kind: (date(2026, 10, 27), []))
    monkeypatch.setattr(E, "_sm_upsert_crypto", lambda adapter, syms: None)
    monkeypatch.setattr(DB, "save_bars", lambda freq, rows: 0)
    monkeypatch.setattr(DB, "save_bars_overwrite", lambda freq, rows: 0)


def _mk(monkeypatch, ad):
    monkeypatch.setattr(E, "_get_supply_adapter", lambda cfg: ad)
    return E._make_crypto_bar_handler(
        "binance_perp_hourly", "bar_minute", "perp", "1h",
        label="binance", enum_hint="test")


def test_first_loop_reraises(env, monkeypatch):
    """首轮循环：软时限直抛（不记 failed、不继续下一标的）。"""
    h = _mk(monkeypatch, _Ad({"BBBUSDT": _FakeSoft("soft limit")}))
    with pytest.raises(_FakeSoft):
        h({"id": "binance_perp_hourly", "mode": "incremental", "retention": None,
           "last_sync_date": None, "provider": "binance"}, "20261029")


def test_second_loop_reraises(env, monkeypatch):
    """二轮补拉：软时限同样直抛（首轮业务失败、二轮换软时限——时限恰落在两轮间）。"""
    state = {"round": 1}

    class _Ad2(_Ad):
        def fetch_supply(self, kind, sub_kind, symbol="", start="", end="", freq=""):
            if symbol == "BBBUSDT":
                if state["round"] == 1:
                    raise _Biz("biz")
                raise _FakeSoft("soft in retry")
            return pd.DataFrame([{"ts": pd.Timestamp("2026-10-28", tz="UTC"), "symbol": symbol}])

    class _Probe(_Ad2):
        """首轮结束后切 round（借 progress_cb 钩子——首轮每标的回调一次，第 3 次即首轮尾）。"""
        def __init__(self):
            super().__init__()
            self.seen = 0

    ad = _Probe()
    orig = ad.fetch_supply

    def _probe_fetch(*a, **k):
        return orig(*a, **k)

    # 切轮钩子：首轮 3 标的跑完（含 BBB 业务失败）→ 二轮开始前 state.round=2。
    # handler 内二轮紧随首轮无钩子——用 failed 记账后首轮尾声不可拦；改为：
    # BBB 二轮才抛软时限，靠 state.round 在「首轮 BBB 抛 Biz 时」就翻 2（首轮抛完
    # 不再触碰 BBB——除二轮补拉），语义=首轮失败记账完成即进入二轮窗口。
    def _fetch(kind, sub_kind, symbol="", start="", end="", freq=""):
        if symbol == "BBBUSDT":
            if state["round"] == 1:
                state["round"] = 2
                raise _Biz("biz")
            raise _FakeSoft("soft in retry")
        return pd.DataFrame([{"ts": pd.Timestamp("2026-10-28", tz="UTC"), "symbol": symbol}])

    ad.fetch_supply = _fetch
    h = _mk(monkeypatch, ad)
    with pytest.raises(_FakeSoft):
        h({"id": "binance_perp_hourly", "mode": "incremental", "retention": None,
           "last_sync_date": None, "provider": "binance"}, "20261029")


def test_normal_failure_still_recorded(env, monkeypatch):
    """普通业务异常不受窄捕获影响：记 failed、任务正常返回。"""
    h = _mk(monkeypatch, _Ad({"BBBUSDT": _Biz("biz")}))
    out = h({"id": "binance_perp_hourly", "mode": "incremental", "retention": None,
             "last_sync_date": None, "provider": "binance"}, "20261029")
    assert any("BBBUSDT" in f for f in out.get("failed_dates", [])), out
    assert out.get("pulled", 0) > 0


def test_budget_break_cursor_capped_to_scanned(monkeypatch):
    """步4盲审 P0（A/B 同撞）回归钉：预算断点后二轮补拉钳已扫段——游标不得越窗。

    场景：跨月两段窗（09-27~10-28 切 2 段）；首段末标的抛业务失败；段预算 mock 为 0
    （首段扫完即 break）；二轮补拉若用全窗 end 会把 reached 推到 10-28 ⇒ 游标越过
    未扫的 10 月段。修复后：补拉窗钳到已扫末段（09-30），cursor_upto<=0930。
    """
    from datetime import date as _d
    import pandas as pd
    import src.data_sync.engine as E
    import src.data_platform.db as DB

    monkeypatch.setattr(E, "_CelerySoftTimeLimit", None, raising=False)
    monkeypatch.setattr(E, "_crypto_end", lambda end_date, adapter, kind: _d(2026, 10, 28))
    monkeypatch.setattr(E, "_crypto_base_start",
                        lambda cfg, bf, adapter, kind: (_d(2026, 9, 27), []))
    monkeypatch.setattr(E, "_sm_upsert_crypto", lambda adapter, syms: None)
    monkeypatch.setattr(DB, "save_bars", lambda freq, rows: 0)

    class _Boom(Exception):
        pass

    class _Ad4:
        provider = "binance"; venue = "BINANCE"
        available_range = staticmethod(lambda kind: ("2019-12-31", None))
        publish_lag = staticmethod(lambda kind: 1)
        list_symbols = staticmethod(lambda: ["AAAUSDT", "ZZZUSDT"])
        fetch_calls = []
        def fetch_supply(self, kind, sub_kind, symbol="", start="", end="", freq=""):
            _Ad4.fetch_calls.append((symbol, start, end))
            if symbol == "AAAUSDT":
                raise _Boom("biz")      # 首段失败 ⇒ 进二轮补拉
            return pd.DataFrame([{"ts": pd.Timestamp("2026-09-30", tz="UTC"), "symbol": symbol}])
        to_bar_rows = staticmethod(lambda df, freq: [
            (s, None, r.ts, None, None, None, None, None, None, None, None, None)
            for s, r in zip(df["symbol"], df.itertuples())])

    monkeypatch.setattr(E, "_get_supply_adapter", lambda cfg: _Ad4())
    # 段预算恒 0：首段（段0）扫完、段边界检查时 break（_seg_idx=1 时 seg_budget_left<=0）
    monkeypatch.setattr(E, "_SEGMENT_BUDGET", 0.0)
    h = E._make_crypto_bar_handler("binance_perp_hourly", "bar_minute", "perp", "1h",
                                   label="binance", enum_hint="test")
    out = h({"id": "binance_perp_hourly", "mode": "incremental", "retention": None,
             "last_sync_date": None, "provider": "binance"}, "20261029")
    # 游标不得越过已扫末段（2026-09-30）——未扫的 10 月段留给下轮
    assert out["cursor_upto"] <= "20260930", out
    # 二轮补拉的窗口上界必须=已扫末段 0930（不是全窗 1028）
    retry_calls = [c for c in _Ad4.fetch_calls if c[0] == "AAAUSDT"]
    assert retry_calls and all(c[2] == "20260930" for c in retry_calls), _Ad4.fetch_calls
