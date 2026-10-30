"""批 157 步 2：区间分段——切分正确性专项（段预算/freeze/游标契约由 softlimit 套与回归套覆盖）。"""
from __future__ import annotations
from datetime import date

import src.data_sync.engine as E


class TestSegmentWindow:
    def test_cross_month(self):
        segs = E._segment_window(date(2026, 9, 20), date(2026, 10, 28))
        assert segs == [(date(2026, 9, 20), date(2026, 9, 30)),
                        (date(2026, 10, 1), date(2026, 10, 28))]

    def test_full_months(self):
        segs = E._segment_window(date(2026, 8, 1), date(2026, 10, 31))
        assert segs == [(date(2026, 8, 1), date(2026, 8, 31)),
                        (date(2026, 9, 1), date(2026, 9, 30)),
                        (date(2026, 10, 1), date(2026, 10, 31))]

    def test_single_day(self):
        assert E._segment_window(date(2026, 10, 28), date(2026, 10, 28)) == [
            (date(2026, 10, 28), date(2026, 10, 28))]

    def test_year_boundary(self):
        segs = E._segment_window(date(2026, 12, 15), date(2027, 1, 5))
        assert segs == [(date(2026, 12, 15), date(2026, 12, 31)),
                        (date(2027, 1, 1), date(2027, 1, 5))]

    def test_empty(self):
        assert E._segment_window(date(2026, 10, 2), date(2026, 10, 1)) == []

    def test_budget_const(self):
        assert E._SEGMENT_BUDGET == 1800.0
