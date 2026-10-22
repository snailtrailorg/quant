"""批 137a：分钟级对账重拉的源格式 + 符号映射（数据正确性级）。

三钉：①`_repull_symbol` 入参 vt ⇒ 源侧收 ts_code（拆归一即红）②crypto 零漂移
③`fetch_supply("bar_minute", None)` 参数名适配（原实现只剩 freq ⇒ TypeError）。
加 ④映射矩阵（`.SSE` 缺失是本批另一处真 bug）。
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest


class _RecAdapter:
    """最小 adapter 替身：记录 fetch_supply 入参。"""

    provider = "tushare"

    def __init__(self, mapper=None):
        self.calls = []
        self._mapper = mapper

    def to_source_symbol(self, symbol: str) -> str:
        return self._mapper(symbol) if self._mapper else symbol

    def fetch_supply(self, kind, sub_kind=None, **kw):
        self.calls.append((kind, sub_kind, kw))
        return pd.DataFrame()


class TestRepullNormalization:
    def test_vt_passed_to_source_as_ts_code(self):
        """接线钉：源侧必须收 ts_code（600000.SH），不是裸码也不是 vt。"""
        from src.data_platform.schema import vt_to_ts
        ad = _RecAdapter(mapper=vt_to_ts)
        from src.data_sync.engine import _repull_symbol
        _repull_symbol(ad, "bar_minute", None, "1min", "600000.SHSE", "20260901", "20260902")
        assert ad.calls[0][2]["symbol"] == "600000.SH"

    def test_crypto_zero_drift(self):
        """crypto 的 to_source_symbol 直通 ⇒ 原值下发（binance 自己的 pull 会 split(".")）。"""
        from src.data_platform.adapters.base import BaseDataAdapter
        ad = _RecAdapter(mapper=BaseDataAdapter.to_source_symbol.__get__(object()))
        from src.data_sync.engine import _repull_symbol
        ad.provider = "binance"
        _repull_symbol(ad, "bar_minute", "perp", "1min", "BTCUSDT.BINANCE", "20260901", "20260902")
        assert ad.calls[0][2]["symbol"] == "BTCUSDT.BINANCE"


class TestSupplyMapping:
    def test_bar_minute_mapped(self):
        """`_SUPPLY_PULL` 必须有 (bar_minute, None)，否则 tushare 直接 UnsupportedFeature。"""
        from src.data_platform.adapters.base import _SUPPLY_PULL
        assert _SUPPLY_PULL.get(("bar_minute", None)) == "pull_minute_supply"

    def test_shell_renames_params(self):
        """薄壳把 symbol/start/end 换成 pull_minute 的 ts_code/start_date/end_date，并补全时间。"""
        from src.data_platform.adapters.base import TushareAdapter
        ad = TushareAdapter()
        with patch("src.data_platform.adapters.tushare_adapter.pull_minute",
                   return_value=pd.DataFrame()) as pm:
            ad.fetch_supply("bar_minute", None, symbol="600000.SH", freq="1min",
                            start="20260901", end="20260902")
        pm.assert_called_once_with("600000.SH", "1min", "20260901 00:00:00", "20260902 23:59:59")

    def test_shell_keeps_full_timestamp(self):
        """已是完整时间戳（含时分秒）时不再补全——防二次拼接。"""
        from src.data_platform.adapters.tushare_adapter import pull_minute_supply
        with patch("src.data_platform.adapters.tushare_adapter.pull_minute",
                   return_value=pd.DataFrame()) as pm:
            pull_minute_supply("600000.SH", "5min", "20260901 09:30:00", "20260901 15:00:00")
        pm.assert_called_once_with("600000.SH", "5min", "20260901 09:30:00", "20260901 15:00:00")


class TestSymbolMatrix:
    def test_sse_alias_now_mapped(self):
        """批 137a 的第二个真 bug：`.SSE` 是本仓合法形态，映射缺失时原样返回。"""
        from src.data_platform.schema import to_ts_code, vt_to_ts
        assert vt_to_ts("600000.SSE") == "600000.SH"
        assert to_ts_code("600000.SSE") == "600000.SH"

    def test_matrix_unchanged(self):
        from src.data_platform.schema import vt_to_ts
        assert vt_to_ts("600000.SHSE") == "600000.SH"
        assert vt_to_ts("000001.SZSE") == "000001.SZ"
        assert vt_to_ts("920196.BSE") == "920196.BJ"
        assert vt_to_ts("600000.SH") == "600000.SH"

    def test_alias_is_single_implementation(self):
        """`vt_to_ts` 必须是 `to_ts_code` 的薄别名（两处实现正是本 bug 被复制的原因）。"""
        import inspect
        from src.data_platform import schema
        src = inspect.getsource(schema.vt_to_ts)
        assert "return to_ts_code(vt_symbol)" in src
        assert "EXCHANGE_TS_MAP.get" not in src