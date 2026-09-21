"""批 58·M3：fetch 契约 + to_contract 测试钉（29 §五/§六验收——bar 族收编）。

钉什么：to_contract 11 字段/UTC 校验（CI 断言二运行时执法）；fetch (kind,sub_kind) 分派
（bar_daily 三品类/bar_minute/index_daily）+ 非 bar 族 UnsupportedFeature + sync_id 不复活
（fetch 签名无 sync_id——用户裁定）。
"""
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest

from src.quant_common.contract import (
    to_contract, ContractFrame, ContractError, DataRequest,
)
from src.data_platform.tz import as_utc


def _row(sym="600000.SHSE", ts=None):
    ts = ts or as_utc(datetime(2026, 9, 21, 9, 31))
    return (sym, "1D", ts, 1.0, 1.0, 1.0, 1.0, 100, 100000.0, 1.0, "tushare")


class TestToContract:
    def test_valid_frame(self):
        f = to_contract([_row()], source="tushare", kind="bar_daily", freq="1D")
        assert f.kind == "bar_daily" and f.source == "tushare" and f.freq == "1D"
        assert len(f.rows) == 1 and f.fetched_at.tzinfo == timezone.utc

    def test_naive_ts_rejected(self):
        r = ("600000.SHSE", "1D", datetime(2026, 9, 21, 9, 31), 1, 1, 1, 1, 100, 100000, 1, "t")
        with pytest.raises(ContractError):
            to_contract([r], source="t", kind="bar_daily", freq="1D")

    def test_wrong_field_count(self):
        with pytest.raises(ContractError):
            to_contract([("a", "b", "c")], source="t", kind="bar_daily", freq="1D")

    def test_empty_rows_ok(self):
        f = to_contract([], source="tushare", kind="bar_daily", freq="1D")
        assert f.rows == () and f.kind == "bar_daily"


class TestFetchDispatch:
    """fetch (kind,sub_kind) 分派——mock pull（不真拉网络）；sync_id 不进签名（用户裁定）。"""

    def _req(self, kind, sub=None, symbols=("600000.SHSE",), start="20260901", end="20260921", freq="1D"):
        return DataRequest(
            kind=kind, symbols=symbols, temporality="historical", sub_kind=sub, freq=freq,
            range_=(datetime(2026, 9, 1), datetime(2026, 9, 21)))

    def test_bar_daily_stock_batch(self):
        from src.data_platform.adapters.base import TushareAdapter
        ad = TushareAdapter()
        df = MagicMock()
        df.empty = False
        with patch.object(ad, "pull_daily_batch", return_value=df) as pb, \
             patch.object(ad, "pull_adj_factor", return_value=None) as paf, \
             patch.object(ad, "to_bar_rows", return_value=[_row()]) as tbr:
            f = ad.fetch(self._req("bar_daily", sub="stock"))
        pb.assert_called_once_with("20260901", "astock")   # sub_kind=stock → 拉取 kind=astock
        paf.assert_called_once_with(trade_date="20260901")  # 当日全市场复权因子
        tbr.assert_called_once_with(df, "1D", {})          # 无因子 → adj_map={}
        assert f.source == "tushare" and f.kind == "bar_daily"

    def test_bar_daily_stock_adj_factor(self):
        """缺口 1：bar_daily+stock 必须把当日复权因子并入 adj_map（astock 因子非 NULL 生死线）。"""
        from src.data_platform.adapters.base import TushareAdapter
        ad = TushareAdapter()
        df = MagicMock()
        df.empty = False
        fdf = pd.DataFrame({"ts_code": ["600000.SH"], "adj_factor": [2.5]})
        with patch.object(ad, "pull_daily_batch", return_value=df), \
             patch.object(ad, "pull_adj_factor", return_value=fdf), \
             patch.object(ad, "to_bar_rows", return_value=[_row()]) as tbr:
            ad.fetch(self._req("bar_daily", sub="stock"))
        tbr.assert_called_once_with(df, "1D", {"600000.SH": 2.5})

    def test_bar_daily_empty_skips_adj_factor(self):
        """缺口 1 加固：空 df（节假日）不拉因子——对齐 _daily_to_save_fn 只在 df 非空时拉。"""
        from src.data_platform.adapters.base import TushareAdapter
        ad = TushareAdapter()
        with patch.object(ad, "pull_daily_batch", return_value=pd.DataFrame()) as pb, \
             patch.object(ad, "pull_adj_factor", return_value=None) as paf, \
             patch.object(ad, "to_bar_rows", return_value=[_row()]):
            ad.fetch(self._req("bar_daily", sub="stock"))
        paf.assert_not_called()

    def test_bar_daily_etf_batch(self):
        from src.data_platform.adapters.base import TushareAdapter
        ad = TushareAdapter()
        with patch.object(ad, "pull_daily_batch", return_value=MagicMock()) as pb, \
             patch.object(ad, "pull_adj_factor", return_value=None), \
             patch.object(ad, "to_bar_rows", return_value=[_row()]):
            ad.fetch(self._req("bar_daily", sub="etf"))
        pb.assert_called_once_with("20260901", "etf")

    def test_bar_minute_segmented(self):
        """缺口 2：bar_minute 必须按 split_minute_range 分段 + 09:00/15:00 约定拉取。"""
        from src.data_platform.adapters.base import TushareAdapter
        ad = TushareAdapter()
        segs = [("20260901", "20260901")]
        with patch("src.data_platform.adapters.tushare_adapter.split_minute_range",
                   return_value=segs) as sr, \
             patch.object(ad, "pull_minute", return_value=pd.DataFrame()) as pm, \
             patch.object(ad, "to_bar_rows", return_value=[_row()]):
            ad.fetch(self._req("bar_minute", freq="1min"))
        sr.assert_called_once_with("20260901", "20260921", "1min")
        pm.assert_called_once_with("600000.SHSE", "1min", "20260901 09:00:00", "20260901 15:00:00")

    def test_bar_daily_convertible_range(self):
        from src.data_platform.adapters.base import TushareAdapter
        ad = TushareAdapter()
        with patch("src.data_platform.adapters.tushare_adapter.pull_cb_daily",
                   return_value=MagicMock()) as pc, \
             patch.object(ad, "to_bar_rows", return_value=[_row()]):
            ad.fetch(self._req("bar_daily", sub="convertible"))
        pc.assert_called_once_with("20260901", "20260921")

    def test_non_bar_unsupported(self):
        from src.data_platform.adapters.base import TushareAdapter, UnsupportedFeature
        with pytest.raises(UnsupportedFeature):
            TushareAdapter().fetch(self._req("fundamental_daily"))

    def test_fetch_signature_has_no_sync_id(self):
        """用户裁定：sync_id 不复活——fetch 签名只有 (req, acct)，无 sync_id 参数。"""
        import inspect
        from src.data_platform.adapters.base import TushareAdapter
        sig = inspect.signature(TushareAdapter.fetch)
        assert "sync_id" not in sig.parameters
