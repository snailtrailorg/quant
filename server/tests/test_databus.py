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
