"""批170：完整性预期根数口径（旧口径导致比例长期上不去）。

旧实现 `expected = max(交易日数, 自然日×5/7)`：
  - 自然日折工作日 0.714 > 真实交易日比率 0.666 ⇒ A 股满分只有 ~93%，complete(≥99%) 不可达；
  - 全市场共用 A 股日历 ⇒ 加密 7×24 被算成 150%+；
  - 分钟线按 A 股 240/日 ⇒ 加密 1440/日 全错。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.data_platform.integrity import (BARS_PER_DAY, CRYPTO_BARS_PER_DAY,
                                        bars_per_day, exchange_of, expected_bars,
                                        is_crypto)


@pytest.fixture(scope="module")
def year_days():
    """2024 全年开市日（近似真实：262 个工作日里有 ~19 天法定假日 ⇒ 约 243 个交易日）。"""
    d = date(2024, 1, 1)
    out = []
    while d.year == 2024:
        # 每 3 周去掉一个工作日，模拟节假日（比例贴近真实 trade_cal）
        if d.weekday() < 5 and d.isocalendar()[1] % 3 != 0:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_exchange_and_crypto_detect():
    assert exchange_of("000001.XSHE") == "SZSE"
    assert exchange_of("BTCUSDT.XBIN") == "BINANCE"
    assert exchange_of("BTCUSDT") == ""
    assert is_crypto("BTCUSDT.XBIN") and is_crypto("ETH-USDT.XOKX")
    assert not is_crypto("510300.XSHG")


def test_astock_perfect_coverage_is_100(year_days):
    """A 股一天不缺 ⇒ 100%（旧口径会被 workday_est 压到 ~93%，永远够不到 complete）。"""
    first, last = year_days[0], year_days[-1]
    exp = expected_bars("1D", "000001.XSHE", first, last, year_days, year_days[0], year_days[-1])
    assert exp == len(year_days)
    assert round(len(year_days) / exp * 100, 1) == 100.0


def test_astock_not_inflated_by_calendar_days(year_days):
    """预期必须等于交易日数，不得被「自然日×5/7」抬高（旧口径的核心错误）。"""
    first, last = year_days[0], year_days[-1]
    exp = expected_bars("1D", "000001.XSHE", first, last, year_days, year_days[0], year_days[-1])
    workday_est = (last - first).days * 5 // 7
    assert exp < workday_est


def test_crypto_uses_247(year_days):
    """加密 7×24：区间自然日全计，不受 A 股日历影响。"""
    first, last = date(2026, 1, 1), date(2026, 1, 31)
    exp = expected_bars("1D", "BTCUSDT.XBIN", first, last, year_days, year_days[0], year_days[-1])
    assert exp == 31


def test_crypto_minute_bars_per_day():
    assert bars_per_day("1min", "BTCUSDT.XBIN") == CRYPTO_BARS_PER_DAY["1min"] == 1440
    assert bars_per_day("1min", "000001.XSHE") == BARS_PER_DAY["1min"] == 240
    exp = expected_bars("1min", "BTCUSDT.XBIN", date(2026, 3, 1), date(2026, 3, 1), [], None, None)
    assert exp == 1440


def test_partial_coverage_extrapolates_with_real_ratio(year_days):
    """区间越过日历覆盖边界：覆盖段精确 + 缺口段按实测开市比率外推（非拍脑袋 5/7）。"""
    first = date(2023, 12, 1)          # 早于日历下界
    last = date(2024, 2, 29)
    exp = expected_bars("1D", "000001.XSHE", first, last, year_days, year_days[0], year_days[-1])
    covered = sum(1 for d in year_days if year_days[0] <= d <= last)
    assert exp >= covered
    assert exp < (last - first).days + 1     # 不会把自然日全算成交易日
