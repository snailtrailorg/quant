"""BaseDataAdapter 测试（24 号多数据源架构）。

金标准：TushareAdapter.to_bar_rows 输出 == 原三个 mapper（逐字节一致）。
"""
import pandas as pd
import pytest

from src.data_platform.adapters.base import (
    TushareAdapter, JoinQuantAdapter, get_adapter, UnsupportedFeature)


def _daily_df():
    return pd.DataFrame([
        {"ts_code": "600000.SH", "trade_date": "2026-01-05", "open": 10.0, "high": 10.5,
         "low": 9.8, "close": 10.2, "vol": 1000, "amount": 10200},
        {"ts_code": "000001.SZ", "trade_date": "2026-01-05", "open": 12.0, "high": 12.3,
         "low": 11.9, "close": 12.1, "vol": 2000, "amount": 24200},
    ])


def _minute_df():
    return pd.DataFrame([
        {"ts_code": "600000.SH", "trade_time": "2026-01-05 09:31:00", "open": 10.0,
         "high": 10.1, "low": 9.9, "close": 10.05, "vol": 100, "amount": 1005},
    ])


def test_to_bar_rows_daily_batch_snapshot():
    """快照：to_bar_rows(df,'1D',adj_map={}) 输出（_daily_to_rows 已收编删除，改快照断言）。"""
    from datetime import datetime
    adapter = TushareAdapter()
    df = _daily_df()
    rows = adapter.to_bar_rows(df, "1D", adj_map={})
    assert rows[0] == ("600000.SHSE", "1D", datetime(2026, 1, 5), 10.0, 10.5, 9.8, 10.2, 1000.0, 10200.0, None, "tushare")
    assert rows[1] == ("000001.SZSE", "1D", datetime(2026, 1, 5), 12.0, 12.3, 11.9, 12.1, 2000.0, 24200.0, None, "tushare")


def test_to_bar_rows_daily_matches_to_save_rows():
    """金标准 2：to_bar_rows(df,'1D') == 原 to_save_rows(df,'1D')。"""
    from src.data_platform.adapters.tushare_adapter import to_save_rows
    adapter = TushareAdapter()
    df = _daily_df()
    assert adapter.to_bar_rows(df, "1D") == to_save_rows(df, "1D")


def test_to_bar_rows_minute_matches_to_save_rows_min():
    """金标准 3：to_bar_rows(df,'1min') == 原 to_save_rows_min(df,'1min')。"""
    from src.data_platform.adapters.tushare_adapter import to_save_rows_min
    adapter = TushareAdapter()
    df = _minute_df()
    assert adapter.to_bar_rows(df, "1min") == to_save_rows_min(df, "1min")


def test_to_bar_rows_adj_map_injection():
    """adj_map 注入：批量路径因子来自 adj_map，source=self.provider。"""
    adapter = TushareAdapter()
    df = _daily_df()
    rows = adapter.to_bar_rows(df, "1D", adj_map={"600000.SH": 1.5})
    assert rows[0][10] == "tushare"         # source
    assert rows[0][9] == 1.5                # adj_factor 来自 adj_map
    assert rows[1][9] is None               # 000001.SZ 不在 adj_map → NULL


def test_to_bar_rows_per_symbol_adj_from_df():
    """per-symbol 路径：adj_map=None 时因子来自 df['adj_factor']。"""
    adapter = TushareAdapter()
    df = _daily_df()
    df["adj_factor"] = [1.2, None]
    rows = adapter.to_bar_rows(df, "1D")
    assert rows[0][9] == 1.2
    assert rows[1][9] is None


def test_get_adapter_routes():
    """注册表路由：tushare→TushareAdapter，joinquant→JoinQuantAdapter，未知→ValueError。"""
    assert isinstance(get_adapter("tushare"), TushareAdapter)
    assert isinstance(get_adapter("joinquant"), JoinQuantAdapter)
    with pytest.raises(ValueError):
        get_adapter("unknown_provider")


def test_stub_raises_not_implemented():
    """stub 不接真源：方法抛 NotImplementedError，证明接口能接。"""
    jq = JoinQuantAdapter()
    with pytest.raises(NotImplementedError):
        jq.pull_daily("000001.XSHE", "20260101", "20260105")
