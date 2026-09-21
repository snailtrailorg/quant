"""批 58·M3：fetch 契约 + to_contract 测试钉（29 §五/§六验收——bar 族收编）。

钉什么：to_contract 11 字段/UTC 校验（CI 断言二运行时执法）；fetch (kind,sub_kind) 分派
（bar_daily 三品类/bar_minute/index_daily）+ 非 bar 族 UnsupportedFeature + sync_id 不复活
（fetch 签名无 sync_id——用户裁定）。
"""
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
        with patch.object(ad, "pull_daily_batch", return_value=df) as pb, \
             patch.object(ad, "to_bar_rows", return_value=[_row()]) as tbr:
            f = ad.fetch(self._req("bar_daily", sub="stock"))
        pb.assert_called_once_with("20260901", "astock")   # sub_kind=stock → 拉取 kind=astock
        tbr.assert_called_once_with(df, "1D")
        assert f.source == "tushare" and f.kind == "bar_daily"

    def test_bar_daily_etf_batch(self):
        from src.data_platform.adapters.base import TushareAdapter
        ad = TushareAdapter()
        with patch.object(ad, "pull_daily_batch", return_value=MagicMock()) as pb, \
             patch.object(ad, "to_bar_rows", return_value=[_row()]):
            ad.fetch(self._req("bar_daily", sub="etf"))
        pb.assert_called_once_with("20260901", "etf")

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
