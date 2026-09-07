"""ts aware 化测试（26 号收尾批 C）。"""
from datetime import datetime, timezone, timedelta

from src.data_platform.tz import as_shanghai, SHANGHAI


def test_naive_to_shanghai():
    """naive → +08:00 aware，墙钟不变。"""
    dt = datetime(2026, 1, 5, 9, 31)
    r = as_shanghai(dt)
    assert r.tzinfo is not None
    assert r.utcoffset() == timedelta(hours=8)
    assert (r.hour, r.minute) == (9, 31)


def test_aware_utc_normalized():
    """aware-UTC → astimezone 归一 +08:00（盲审 B-P2：不原样返回）。"""
    dt = datetime(2026, 1, 5, 1, 31, tzinfo=timezone.utc)
    r = as_shanghai(dt)
    assert (r.hour, r.minute) == (9, 31)   # UTC 01:31 → +08:00 09:31


def test_aware_shanghai_unchanged():
    """已 Asia/Shanghai 原样返回。"""
    dt = datetime(2026, 1, 5, 9, 31, tzinfo=SHANGHAI)
    assert as_shanghai(dt) == dt


def test_to_bar_rows_ts_aware():
    """to_bar_rows 输出 ts 带 +08:00 aware。"""
    import pandas as pd
    from src.data_platform.adapters.base import TushareAdapter
    df = pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "20260105",
                        "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
                        "vol": 1000, "amount": 10200}])
    rows = TushareAdapter().to_bar_rows(df, "1D", adj_map={})
    assert rows[0][2].tzinfo is not None
    assert rows[0][2].utcoffset() == timedelta(hours=8)
