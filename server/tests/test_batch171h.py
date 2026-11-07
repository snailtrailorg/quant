"""批 171h · DEF-16 日历末端守卫 + DEF-06 _local_dates_map 下界守门。

DEF-16 两向直证（截断⇒None / end−3 内⇒正常）；DEF-06 since 超集投影等价（真库）
+None 零漂移。既有 test_batch97 连锁修补另文件（见该文件 171h 注记）。
"""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.data_sync import engine


def _cal_rows(d0: date, d1: date, *, is_open=True):
    """构造 trade_cal 形态行集 [(date, is_open)]——自然日逐行（含非交易日，dev 实证形态）。"""
    out, d = [], d0
    while d <= d1:
        out.append((d, is_open and d.weekday() < 5))
        d += timedelta(days=1)
    return out


def _run_cal(monkeypatch, rows):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = rows
    monkeypatch.setattr(engine, "get_conn", MagicMock(return_value=conn))
    s = (date(2026, 10, 1) + timedelta(days=0)).strftime("%Y%m%d")
    e = date(2026, 10, 9).strftime("%Y%m%d")
    return engine._trade_dates_in_range("20261001", "20261009")


class TestCalendarEndGuard:

    def _cal(self, monkeypatch, last_row: date):
        """patch 源头 `_db.get_conn`（函数内局部 import 每次调用重取）——双层 with
        （conn + cursor）的 enter/exit 全配 MagicMock。行集含 09-30（起点前一自然日，
        贴真实 BETWEEN 语义=窗内含前日行——期望须含 9-30 周三开市日）。"""
        rows = _cal_rows(date(2026, 9, 30), last_row)
        cur = MagicMock()
        cur.fetchall.return_value = rows
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.__exit__.return_value = False
        conn.cursor.return_value.__enter__.return_value = cur
        conn.cursor.return_value.__exit__.return_value = False
        import src.data_platform.db as _db
        monkeypatch.setattr(_db, "get_conn", MagicMock(return_value=conn))

    def test_end_truncated_returns_none(self, monkeypatch, caplog):
        """末行 10-05 距 end 10-09 = 4 天 ⇒ None（截断拦截）。"""
        self._cal(monkeypatch, date(2026, 10, 5))
        with caplog.at_level("WARNING"):
            got = engine._trade_dates_in_range("20261001", "20261009")
        assert got is None
        assert any("末端覆盖不足" in r.message for r in caplog.records)

    def test_end_within_3d_returns_days(self, monkeypatch):
        """末行 10-07（周三）距 end 10-09（周五）=2 天 ⇒ 正常列表（2026-10-01=周四）。"""
        self._cal(monkeypatch, date(2026, 10, 7))
        got = engine._trade_dates_in_range("20261001", "20261009")
        assert got == ["20260930", "20261001", "20261002", "20261005", "20261006", "20261007"]  # 9-30 Wed..10-07（mock 行集含前日行）

    def test_end_on_weekend_3d_tolerance(self, monkeypatch):
        """end=周六、末行=周五（库维护到周五）=1 天 ⇒ 正常（end 落周末常态）。"""
        self._cal(monkeypatch, date(2026, 10, 9))   # 10-09=周五
        got = engine._trade_dates_in_range("20261001", "20261010")   # end=10-10 周六
        assert got == ["20260930", "20261001", "20261002", "20261005", "20261006", "20261007", "20261008", "20261009"]


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM sync_config LIMIT 1")
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")


class TestLocalDatesMapSince:

    @needs_db
    def test_since_superset_projection_equivalence(self):
        """since 下界 ⇒ 返回=全集在 [since,∞) 的投影（超集语义——expected 消费零漂移）。"""
        syms = ["600000.SHSE", "000001.SZSE"]
        dexpr = "to_char(ts AT TIME ZONE 'Asia/Shanghai','YYYYMMDD')"
        full = engine._local_dates_map("bar_1d", dexpr, syms)
        cut = engine._local_dates_map("bar_1d", dexpr, syms, since="20100101")
        assert full is not None and cut is not None
        for s in syms:
            expect = {d for d in full.get(s, set()) if d >= "20100101"}
            assert cut.get(s, set()) == expect, f"{s}: 投影逐值相等"

    @needs_db
    def test_since_none_zero_drift(self):
        """since=None ⇒ 与旧签名逐字同值（零漂移铁律）。"""
        syms = ["600000.SHSE"]
        dexpr = "to_char(ts AT TIME ZONE 'Asia/Shanghai','YYYYMMDD')"
        a = engine._local_dates_map("bar_1d", dexpr, syms)
        b = engine._local_dates_map("bar_1d", dexpr, syms, since=None)
        assert a == b
