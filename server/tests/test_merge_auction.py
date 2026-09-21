"""批 58b：merge_auction_into_first 契约测试钉（28 §3.2 竞价条并入首根）。

钉什么：09:30 竞价条并入 09:31（volume/amount 累加 + open 取竞价条 + high/low 取 max/min）+ 删 09:30 条；
无竞价条/空 df 幂等。验收②的样本级对账前置。
"""
import pandas as pd


def _df(rows):
    df = pd.DataFrame(rows)
    if "adj_factor" not in df.columns:
        df["adj_factor"] = None
    if "trade_date" not in df.columns:
        df["trade_date"] = df["trade_time"].str[:10].str.replace("-", "")
    return df


def _hhmm(df):
    return df["trade_time"].str[11:16]


def test_merge_auction_into_first():
    from src.data_platform.adapters.tushare_adapter import merge_auction_into_first
    df = _df([
        {"ts_code": "600000.SH", "trade_time": "2026-09-21 09:30:00",
         "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "vol": 1000.0, "amount": 10000.0},
        {"ts_code": "600000.SH", "trade_time": "2026-09-21 09:31:00",
         "open": 10.3, "high": 10.4, "low": 10.1, "close": 10.35, "vol": 500.0, "amount": 5000.0},
        {"ts_code": "600000.SH", "trade_time": "2026-09-21 09:32:00",
         "open": 10.4, "high": 10.5, "low": 10.3, "close": 10.4, "vol": 300.0, "amount": 3000.0},
    ])
    out = merge_auction_into_first(df)
    # 09:30 条被删，剩 2 行
    assert (_hhmm(out) == "09:30").sum() == 0
    assert len(out) == 2
    first = out[_hhmm(out) == "09:31"].iloc[0]
    # 并入：volume=500+1000、amount=5000+10000、open=竞价 open、high/low 取 max/min
    assert first["vol"] == 1500.0
    assert first["amount"] == 15000.0
    assert first["open"] == 10.0
    assert first["high"] == 10.5
    assert first["low"] == 9.8


def test_merge_multi_symbol_days():
    from src.data_platform.adapters.tushare_adapter import merge_auction_into_first
    df = _df([
        {"ts_code": "600000.SH", "trade_time": "2026-09-18 09:30:00",
         "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "vol": 10.0, "amount": 100.0},
        {"ts_code": "600000.SH", "trade_time": "2026-09-18 09:31:00",
         "open": 1.1, "high": 1.1, "low": 1.0, "close": 1.05, "vol": 20.0, "amount": 200.0},
        {"ts_code": "600000.SH", "trade_time": "2026-09-21 09:30:00",
         "open": 2.0, "high": 2.2, "low": 1.9, "close": 2.1, "vol": 30.0, "amount": 300.0},
        {"ts_code": "600000.SH", "trade_time": "2026-09-21 09:31:00",
         "open": 2.1, "high": 2.1, "low": 2.0, "close": 2.05, "vol": 40.0, "amount": 400.0},
    ])
    out = merge_auction_into_first(df)
    assert (_hhmm(out) == "09:30").sum() == 0
    assert len(out) == 2
    d18 = out[out["trade_date"] == "20260918"].iloc[0]
    d21 = out[out["trade_date"] == "20260921"].iloc[0]
    assert d18["vol"] == 30.0 and d18["open"] == 1.0   # 09-18 并入
    assert d21["vol"] == 70.0 and d21["open"] == 2.0   # 09-21 并入（按日独立）


def test_merge_no_auction_idempotent():
    from src.data_platform.adapters.tushare_adapter import merge_auction_into_first
    df = _df([
        {"ts_code": "600000.SH", "trade_time": "2026-09-21 09:31:00",
         "open": 10.3, "high": 10.4, "low": 10.1, "close": 10.35, "vol": 500.0, "amount": 5000.0},
    ])
    out = merge_auction_into_first(df)
    assert len(out) == 1 and out.iloc[0]["vol"] == 500.0


def test_merge_empty():
    from src.data_platform.adapters.tushare_adapter import merge_auction_into_first
    assert merge_auction_into_first(pd.DataFrame()).empty
    assert merge_auction_into_first(None) is None
