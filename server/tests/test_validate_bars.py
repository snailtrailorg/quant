"""A2 数据质量校验单测（#30）：validate_bars 剔 ohlc=0 + 标 ts 断点（不剔）。

自包含验证：本测试只依赖接口契约 §Bar（rows 11 字段）+ validate_bars 签名（接口契约 §三②）。
"""
from datetime import datetime


def _row(ts, o=10.0, h=10.0, l=10.0, c=10.0, sym="600000.SHSE"):
    """造一行 11 字段 bar row（ohlc 默认 10，正常）。"""
    return (sym, "1D", ts, o, h, l, c, 100.0, 1000.0, None, "tushare")


def test_validate_bars_removes_zero_ohlc():
    """ohlc=0 的行被剔除，正常行保留。"""
    from src.data_platform.db import validate_bars
    ts = datetime(2026, 8, 8)
    rows = [_row(ts, 0, 0, 0, 0), _row(ts)]  # 第 1 行坏，第 2 行正常
    result = validate_bars(rows)
    assert len(result) == 1
    assert result[0][3] == 10.0  # 保留正常行


def test_validate_bars_keeps_gap_warning():
    """ts 断点（>7 天）记 warning，不剔除。"""
    from src.data_platform.db import validate_bars
    ts1 = datetime(2026, 8, 1)
    ts2 = datetime(2026, 8, 9)  # 距 ts1 8 天（>7 断点）
    rows = [_row(ts1), _row(ts2)]
    result = validate_bars(rows)
    assert len(result) == 2  # 不剔，只 warning


def test_validate_bars_keeps_normal():
    """正常行全保留（顺序不变）。"""
    from src.data_platform.db import validate_bars
    ts1 = datetime(2026, 8, 1)
    ts2 = datetime(2026, 8, 2)  # 相邻 1 天，无断点
    rows = [_row(ts1), _row(ts2)]
    result = validate_bars(rows)
    assert len(result) == 2
    assert result == rows
