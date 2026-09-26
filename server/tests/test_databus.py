"""批 59·M4：DataBus + Store 测试钉（local_pg 候选 + fetch-on-miss + 版本冻结）。

钉什么：get_bars 的 local_pg 命中/空→DataGap→远端 fetch-on-miss 落仓；store cur_version/
frozen_or_current 三态；_local_fetch 的 db.get_bars 包装（空抛 DataGap、非空 to_contract）。
"""
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest

from src.quant_common.contract import DataRequest, DataGap, ContractFrame
from src.data_platform.tz import as_utc


def _req(kind="bar_daily", symbol="600000.SHSE", freq="1D"):
    return DataRequest(kind=kind, symbols=(symbol,), temporality="historical", freq=freq,
                       range_=(datetime(2025, 9, 21), datetime(2026, 9, 21)))


def _bar_df(n=2):
    ts = as_utc(datetime(2026, 9, 18))
    rows = [("600000.SHSE", "1D", ts, 10.0, 10.5, 9.8, 10.2, 1000.0, 10000.0, 1.0, "tushare")
            for _ in range(n)]
    return pd.DataFrame(rows, columns=["symbol", "freq", "ts", "open", "high", "low",
                                       "close", "volume", "amount", "adj_factor", "source"])


def _frame():
    from src.quant_common.contract import to_contract
    rows = [("600000.SHSE", "1D", as_utc(datetime(2026, 9, 18)), 10.0, 10.5, 9.8, 10.2,
             1000.0, 10000.0, 1.0, "tushare")]
    return to_contract(rows, source="tushare", kind="bar_daily", freq="1D")


class TestStore:
    def _conn(self, value):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = MagicMock()
        cur.fetchone.return_value = None if value is None else (value,)
        conn.execute.return_value = cur
        return conn

    def test_cur_version_default(self):
        from src.data_platform.store import Store
        with patch("src.data_platform.store.get_conn", return_value=self._conn(None)):
            assert Store().cur_version("bar_daily") == 1

    def test_cur_version_set(self):
        from src.data_platform.store import Store
        with patch("src.data_platform.store.get_conn", return_value=self._conn("2")):
            assert Store().cur_version("bar_daily") == 2

    def test_frozen_or_current_frozen(self):
        """冻结键在位→返回冻结版本（不查 cur_version）。"""
        from src.data_platform.store import Store
        with patch("src.data_platform.store.get_conn", return_value=self._conn("1")):
            assert Store().frozen_or_current("bar_daily") == 1

    def test_frozen_or_current_no_frozen(self):
        """无冻结键→回落 cur_version（冻结键 None，版本键 "2"）。"""
        from src.data_platform.store import Store
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = MagicMock()
        cur.fetchone.side_effect = [None, ("2",)]   # 第一次=冻结键 None，第二次=版本键 "2"
        conn.execute.return_value = cur
        with patch("src.data_platform.store.get_conn", return_value=conn):
            assert Store().frozen_or_current("bar_daily") == 2


class TestDataBus:
    def test_get_bars_local_hit(self):
        from src.data_platform.databus import DataBus
        bus = DataBus()
        frame = _frame()
        with patch.object(bus, "_local_fetch", return_value=frame) as lf, \
             patch("src.data_platform.routing.resolve") as rs:
            out, wm = bus.get_bars(_req())
        lf.assert_called_once()
        assert out is frame and wm is not None

    def test_get_bars_miss_degrades_empty(self):
        """local 空→fetch-on-miss 本批降级空帧（adapter 无 per-symbol 区间拉取，挂账——不崩）。"""
        from src.data_platform.databus import DataBus
        bus = DataBus()
        with patch.object(bus, "_local_fetch", side_effect=DataGap("无")):
            out, wm = bus.get_bars(_req())
        assert out.rows == () and out.source == "local_pg" and wm is None

    def test_local_fetch_empty_raises_data_gap(self):
        from src.data_platform.databus import DataBus
        bus = DataBus()
        with patch("src.data_platform.db.get_bars", return_value=pd.DataFrame()):
            with pytest.raises(DataGap):
                bus._local_fetch(_req())

    def test_local_fetch_hit_returns_frame(self):
        from src.data_platform.databus import DataBus
        bus = DataBus()
        with patch("src.data_platform.db.get_bars", return_value=_bar_df()):
            frame = bus._local_fetch(_req())
        assert frame.source == "local_pg" and len(frame.rows) == 2

    def test_get_reference_data(self):
        from src.data_platform.databus import DataBus
        bus = DataBus()
        chain = MagicMock()
        chain.fetch.return_value = _frame()
        with patch("src.data_platform.routing.resolve", return_value=chain):
            out = bus.get("bar_daily", _req())
        assert out is chain.fetch.return_value


def _min_frame(minutes):
    """分钟 bar 帧（2026-09-22 上午，分钟末标注 ts=10:m:00 UTC 锚测试——实产为上海本地对应 UTC）。"""
    from src.quant_common.contract import to_contract
    rows = [("600000.SHSE", "1min", as_utc(datetime(2026, 9, 22, 10, m)), 10.0, 10.5, 9.8, 10.2,
             1000.0, 10000.0, 1.0, "hub") for m in minutes]
    return to_contract(rows, source="local_pg", kind="bar_minute", freq="1min")


def _xrev(minutes, dup_ts=None):
    """xrevrange 返回（新→旧）；dup_ts 分钟重复一次（同 ts 双 gen 交界）。"""
    entries, seq = [], 0
    for m in reversed(minutes):
        ts = as_utc(datetime(2026, 9, 22, 10, m)).isoformat()
        seq += 1
        entries.append((f"1-{seq}", {"gen": "163", "seq": str(seq), "ts": ts, "close": "10.0"}))
        if m == dup_ts:
            seq += 1
            entries.append((f"1-{seq}", {"gen": "164", "seq": "1", "ts": ts, "close": "10.0"}))
    return entries


class TestSubscribe:
    """M5 subscribe 真实现（批 60 C3）：回放截断+去重+未达降级+poll。"""

    def test_replay_truncate_and_dedupe(self):
        """水位线截断（严格新于）+ ts 去重（同 ts 留新 gen）+ poll 起点最新 id。"""
        from src.quant_common.contract import Subscription
        from src.data_platform.databus import DataBus
        bus = DataBus()
        r = MagicMock()
        rows = _xrev([1, 2, 3, 4, 5], dup_ts=4)
        r.xrevrange.return_value = rows
        wm = as_utc(datetime(2026, 9, 22, 10, 3))
        sub = Subscription(kind="bar_minute", symbols=("600000.SHSE",), account_id=1, from_watermark=wm)
        with patch("src.data_platform.databus._r", return_value=r):
            h = bus.subscribe(sub)
        tss = [datetime.fromisoformat(m["ts"]).minute for m in h.bars]
        assert tss == [4, 5]            # 严格新于 10:03 + 去重（gen 163/164 同 ts 只留一）
        assert h.bars[0]["gen"] == "164"  # 同 ts 留新 gen（切换交界信号保留）
        assert h.warmup is None
        assert h.last_id == rows[0][0]  # poll 起点=流内最新 id
        assert h.stream_key == "hub:bars:1:600000.SHSE"

    def test_multi_symbol_fails_fast(self):
        from src.quant_common.contract import Subscription
        from src.data_platform.databus import DataBus
        bus = DataBus()
        sub = Subscription(kind="bar_minute", symbols=("a.SHSE", "b.SHSE"), account_id=1)
        with pytest.raises(ValueError):
            bus.subscribe(sub)

    def test_naive_watermark_does_not_crash(self):
        """naive 水位线 → 按上海本地解释转 aware（不 TypeError）。"""
        from src.quant_common.contract import Subscription
        from src.data_platform.databus import DataBus
        bus = DataBus()
        r = MagicMock()
        r.xrevrange.return_value = _xrev([1, 2, 3])
        sub = Subscription(kind="bar_minute", symbols=("600000.SHSE",), account_id=1,
                           from_watermark=datetime(2026, 9, 22, 10, 1))  # naive
        with patch("src.data_platform.databus._r", return_value=r):
            h = bus.subscribe(sub)
        assert [datetime.fromisoformat(m["ts"]).minute for m in h.bars] == [2, 3]

    def test_partial_underrun_falls_back(self):
        """回放窗头未接上水位线（有洞）→ 也降级 warmup（不只「空回放」触发）。"""
        from src.quant_common.contract import Subscription
        from src.data_platform.databus import DataBus
        bus = DataBus()
        r = MagicMock()
        r.xrevrange.return_value = _xrev([8, 9, 10])          # 窗头 10:08，wm=10:01 → 洞
        wm = as_utc(datetime(2026, 9, 22, 10, 1))
        sub = Subscription(kind="bar_minute", symbols=("600000.SHSE",), account_id=1, from_watermark=wm)
        frame = _frame()
        with patch("src.data_platform.databus._r", return_value=r), \
             patch.object(bus, "_local_fetch", return_value=frame):
            h = bus.subscribe(sub)
        assert h.warmup is frame

    def test_poll_nonblocking_default(self):
        """poll 默认 block=None（不拼 BLOCK，非阻塞），而非 block=0（redis BLOCK 0=无限阻塞）。"""
        from src.quant_common.contract import Subscription
        from src.data_platform.databus import DataBus
        bus = DataBus()
        r = MagicMock()
        r.xrevrange.return_value = []
        sub = Subscription(kind="bar_minute", symbols=("600000.SHSE",), account_id=1)
        with patch("src.data_platform.databus._r", return_value=r):
            h = bus.subscribe(sub)
        h.poll()
        assert r.xread.call_args[1]["block"] is None

    def test_replay_no_watermark_returns_all(self):
        from src.quant_common.contract import Subscription
        from src.data_platform.databus import DataBus
        bus = DataBus()
        r = MagicMock()
        r.xrevrange.return_value = _xrev([1, 2, 3])
        sub = Subscription(kind="bar_minute", symbols=("600000.SHSE",), account_id=1, from_watermark=None)
        with patch("src.data_platform.databus._r", return_value=r):
            h = bus.subscribe(sub)
        assert [datetime.fromisoformat(m["ts"]).minute for m in h.bars] == [1, 2, 3]
        assert h.warmup is None

    def test_underrun_falls_back_to_get_bars(self):
        """回放未达水位线（剪尾/断流）→ 告警 + 降级 get_bars 补 warmup 帧。"""
        from src.quant_common.contract import Subscription
        from src.data_platform.databus import DataBus
        bus = DataBus()
        r = MagicMock()
        r.xrevrange.return_value = _xrev([1, 2, 3])
        wm = as_utc(datetime(2026, 9, 22, 15, 0))   # 水位线超前于流内全部根
        sub = Subscription(kind="bar_minute", symbols=("600000.SHSE",), account_id=1, from_watermark=wm)
        frame = _frame()
        with patch("src.data_platform.databus._r", return_value=r), \
             patch.object(bus, "_local_fetch", return_value=frame) as lf:
            h = bus.subscribe(sub)
        assert h.bars == [] and h.warmup is frame
        assert lf.call_args[0][0].kind == "bar_minute"   # freq 按 kind 推断
        assert lf.call_args[0][0].range_[1] is not None  # end 不传 None（盲审 A：end=None 落 SQL ts<=NULL 恒空）

    def test_poll_advances_last_id_and_survives_error(self):
        from src.quant_common.contract import Subscription
        from src.data_platform.databus import DataBus
        bus = DataBus()
        r = MagicMock()
        r.xrevrange.return_value = _xrev([1])
        sub = Subscription(kind="bar_minute", symbols=("600000.SHSE",), account_id=1)
        with patch("src.data_platform.databus._r", return_value=r):
            h = bus.subscribe(sub)
        r.xread.return_value = [("hub:bars:1:600000.SHSE", [("1-9", {"gen": "163", "ts": "x", "close": "1"})])]
        assert len(h.poll()) == 1 and h.last_id == "1-9"
        r.xread.side_effect = Exception("down")
        assert h.poll() == []          # 失败不崩，下次再试
        h.close()
        assert h.poll() == []          # close 后静默


class TestWatermark:
    """M5 连续无缺水位线（分钟网格锚：午休/日界/缺口）。"""

    def test_continuous_minutes(self):
        from src.data_platform.databus import DataBus
        assert DataBus._watermark(_min_frame([1, 2, 3]), "1min").minute == 3

    def test_gap_stops_before_hole(self):
        from src.data_platform.databus import DataBus
        assert DataBus._watermark(_min_frame([1, 2, 5]), "1min").minute == 2

    def test_lunch_anchor_bridges(self):
        """11:30 → 13:01 午休锚=期望衔接（缺口=13:01 起回走跨锚连续）。"""
        from src.quant_common.contract import to_contract
        from src.data_platform.databus import DataBus
        rows = [("600000.SHSE", "1min", as_utc(datetime(2026, 9, 22, 11, 30)), 1, 1, 1, 1, 1, 1, 1, "hub"),
                ("600000.SHSE", "1min", as_utc(datetime(2026, 9, 22, 13, 1)), 1, 1, 1, 1, 1, 1, 1, "hub")]
        wm = DataBus._watermark(to_contract(rows, source="hub", kind="bar_minute", freq="1min"), "1min")
        assert wm == as_utc(datetime(2026, 9, 22, 13, 1))   # as_utc=按上海本地解释（11:30→13:01 跨锚连续）

    def test_daily_freq_keeps_max_ts(self):
        """日线无交易日历网格 → 最大 ts 语义（挂账 daily 网格需 market_hours）。"""
        from src.data_platform.databus import DataBus
        assert DataBus._watermark(_frame(), "1D") == as_utc(datetime(2026, 9, 18))
