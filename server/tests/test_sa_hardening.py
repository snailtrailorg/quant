"""SA 稳定性加固测试（SA1/SA2/SA3，2026-08-17 稳定性检查 F-26/F-24/F-25/F-18/F-13）。

覆盖：
- _guard：handler 异常拦截后事件线程语义存活（F-26 降级）
- _in_astock_session：时段边界 + 节假日安全性（配合"今日已收 tick"条件）
- _sd_notify：无 NOTIFY_SOCKET 静默（本地/手工运行不炸）
- alert_failed：OnFailure 钩子走通知中心，notify 抛异常不影响退出码（F-13）
"""
import datetime as dt
import logging
from unittest.mock import patch

from src.strategy_runner.main import _guard, _in_astock_session, _sd_notify


class TestGuard:
    def test_exception_not_propagated(self):
        """F-26 核心：handler 抛异常不上抛（EventEngine 只捕 Empty，上抛=线程死亡）。"""

        @_guard("test")
        def boom():
            raise ValueError("炸")

        boom()  # 不应 raise

    def test_handler_survives_after_exception(self):
        calls = []

        @_guard("test")
        def fn(x):
            calls.append(x)
            if x == 1:
                raise RuntimeError("第一次炸")
            return x * 10

        assert fn(1) is None  # 异常路径无返回值
        assert fn(2) == 20  # 第二次正常执行
        assert calls == [1, 2]

    def test_alert_failure_does_not_break_guard(self):
        """guard 内 _alert 失败（如通知中心不可用）也绝不能把异常再抛出去。"""
        with patch("src.strategy_runner.main._alert", side_effect=RuntimeError("通知中心挂了")):

            @_guard("t")
            def boom():
                raise ValueError("业务异常")

            boom()  # 通知挂了也不上抛


class TestSession:
    def test_boundaries(self):
        assert not _in_astock_session(dt.datetime(2026, 8, 17, 9, 30))   # 开盘前
        assert _in_astock_session(dt.datetime(2026, 8, 17, 9, 31))
        assert _in_astock_session(dt.datetime(2026, 8, 17, 11, 30))
        assert not _in_astock_session(dt.datetime(2026, 8, 17, 12, 0))   # 午休
        assert _in_astock_session(dt.datetime(2026, 8, 17, 13, 1))
        assert _in_astock_session(dt.datetime(2026, 8, 17, 15, 0))
        assert not _in_astock_session(dt.datetime(2026, 8, 17, 15, 1))   # 收盘后

    def test_weekend(self):
        assert not _in_astock_session(dt.datetime(2026, 8, 15, 10, 0))  # 周六
        assert not _in_astock_session(dt.datetime(2026, 8, 16, 14, 0))  # 周日


class TestSdNotify:
    def test_silent_without_socket(self, monkeypatch):
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        _sd_notify("WATCHDOG=1")  # 不应抛异常

    def test_bad_socket_silent(self, monkeypatch):
        monkeypatch.setenv("NOTIFY_SOCKET", "/nonexistent/socket/path")
        _sd_notify("WATCHDOG=1")  # 连接失败静默


class TestAlertFailed:
    # 包 __init__ re-export 了同名函数 notify，遮蔽子模块——必须 importlib 拿真模块再 patch
    @staticmethod
    def _notify_mod():
        import importlib
        return importlib.import_module("src.alert_notify.notify")

    def test_calls_notify_with_unit(self, caplog):
        from src.strategy_runner import alert_failed
        with patch.object(self._notify_mod(), "notify", return_value=99) as m:
            with patch("sys.argv", ["alert_failed", "quant-live-task@9.service"]):
                alert_failed.main()
        m.assert_called_once()
        args = m.call_args[0]
        assert args[0] == "critical" and args[1] == "system"
        assert "quant-live-task@9.service" in args[2]

    def test_notify_failure_only_logs(self, caplog):
        from src.strategy_runner import alert_failed
        with patch.object(self._notify_mod(), "notify", side_effect=RuntimeError("PG 挂")):
            with patch("sys.argv", ["alert_failed", "x.service"]):
                alert_failed.main()  # 不应 raise
        assert any(r.levelno >= logging.ERROR for r in caplog.records)
