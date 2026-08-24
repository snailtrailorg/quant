"""MdSession 契约测试（L2 会话层，2026-08-24 韧性分层模型）。

覆盖：
- _zombie_session：零 tick 宽限 / 有 tick 不判死 / 非交易日 / 非时段 / 边界
- XtpMdSession：schedule_due 时刻/交易日/去重、retry_ready 退避
- is_trading_day：日历优先、工作日 fallback
"""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import time

import pytest

from src.strategy_framework.md_session import is_trading_day, zombie_session


# ── is_trading_day ──

class TestIsTradingDay:
    def test_trading_day_from_calendar(self):
        """日历返回 True -> True。"""
        with patch("src.data_platform.platform.is_trading_day", return_value=True):
            assert is_trading_day(datetime(2026, 8, 24)) is True

    def test_holiday_from_calendar(self):
        """日历返回 False -> False。"""
        with patch("src.data_platform.platform.is_trading_day", return_value=False):
            assert is_trading_day(datetime(2026, 8, 24)) is False

    def test_calendar_fails_weekday_fallback(self):
        """日历异常时周一->True，周六->False。"""
        with patch("src.data_platform.platform.is_trading_day", side_effect=RuntimeError):
            assert is_trading_day(datetime(2026, 8, 24)) is True   # 周一
            assert is_trading_day(datetime(2026, 8, 22)) is False  # 周六


# ── zombie_session ──

class TestZombieSession:
    NOW = 1000000.0

    def test_zero_tick_beyond_grace(self):
        """交易时段 + 交易日 + 零 tick 超 10 分钟 -> True。"""
        assert zombie_session(True, 0, self.NOW - 700, self.NOW, True, grace=600) is True

    def test_grace_not_elapsed(self):
        """零 tick 但未超宽限 -> False。"""
        assert zombie_session(True, 0, self.NOW - 300, self.NOW, True, grace=600) is False

    def test_has_ticks(self):
        """有过 tick（sess_ticks>0）-> False（中途断流走另一分支）。"""
        assert zombie_session(True, 5, self.NOW - 700, self.NOW, True, grace=600) is False

    def test_non_trading_day(self):
        """非交易日（假日）-> False（防假日零 tick 误判自愈）。"""
        assert zombie_session(True, 0, self.NOW - 700, self.NOW, False, grace=600) is False

    def test_non_session(self):
        """非交易时段 -> False（午休/盘后）。"""
        assert zombie_session(False, 0, self.NOW - 700, self.NOW, True, grace=600) is False

    def test_sess_enter_ts_zero(self):
        """sess_enter_ts=0（未初始化，如盘中启动未设起点）-> False。"""
        assert zombie_session(True, 0, 0.0, self.NOW, True, grace=600) is False


# ── XtpMdSession ──

class TestXtpMdSession:
    def test_schedule_due_on_time(self):
        """交易日 09:10 前 -> False；09:10 后 -> True（当日去重）。"""
        from src.strategy_framework.md_session import XtpMdSession
        sess = XtpMdSession(MagicMock())
        with patch("src.strategy_framework.md_session.is_trading_day", return_value=True):
            assert sess.schedule_due(datetime(2026, 8, 24, 9, 9)) is False
            assert sess.schedule_due(datetime(2026, 8, 24, 9, 10)) is True
            assert sess.schedule_due(datetime(2026, 8, 24, 9, 11)) is False  # 当日去重

    def test_schedule_due_non_trading_day(self):
        """非交易日 -> False。"""
        from src.strategy_framework.md_session import XtpMdSession
        sess = XtpMdSession(MagicMock())
        with patch("src.strategy_framework.md_session.is_trading_day", return_value=False):
            assert sess.schedule_due(datetime(2026, 8, 24, 9, 10)) is False

    def test_renew_calls_login_server(self):
        """renew() -> md_api.login_server()。"""
        from src.strategy_framework.md_session import XtpMdSession
        md = MagicMock()
        sess = XtpMdSession(md)
        assert sess.renew() is True
        md.login_server.assert_called_once()

    def test_renew_md_none(self):
        """md_api 不存在 -> False。"""
        from src.strategy_framework.md_session import XtpMdSession
        sess = XtpMdSession(None)
        assert sess.renew() is False

    def test_retry_ready_first(self):
        """首次 retry_ready（last_retry_ts=0）-> True（立即触发）。"""
        from src.strategy_framework.md_session import XtpMdSession
        sess = XtpMdSession(MagicMock())
        assert sess.retry_ready(0) is True

    def test_retry_ready_backoff(self):
        """renew 后 5s -> False（60s 退避未到点）。"""
        from src.strategy_framework.md_session import XtpMdSession
        sess = XtpMdSession(MagicMock())
        sess.renew()  # 设 last_retry_ts=now, backoff=60
        assert sess.retry_ready(sess._last_retry_ts + 5) is False

    def test_retry_ready_elapsed(self):
        """renew 后 65s -> True（60s 退避已过）。"""
        from src.strategy_framework.md_session import XtpMdSession
        sess = XtpMdSession(MagicMock())
        sess.renew()  # backoff=30 -> 60
        assert sess.retry_ready(sess._last_retry_ts + 65) is True

    def test_on_recovered_resets(self):
        """on_recovered -> backoff 回归起始值 30s。"""
        from src.strategy_framework.md_session import XtpMdSession
        sess = XtpMdSession(MagicMock())
        sess.renew()  # backoff=30 -> 60
        sess.renew()  # 60 -> 120
        assert sess._backoff == 120
        sess.on_recovered()
        assert sess._backoff == 30

    def test_renew_escalates_backoff(self):
        """连续 renew 退避翻倍封顶 300s。"""
        from src.strategy_framework.md_session import XtpMdSession
        sess = XtpMdSession(MagicMock())
        for _ in range(5):
            sess.renew()
        assert sess._backoff == 300