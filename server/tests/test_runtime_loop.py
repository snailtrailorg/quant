"""EngineLoop 到期驱动测试（批 2）——假时钟零真实等待。"""
from unittest.mock import MagicMock, patch


class FakeClock:
    """可注入时钟：now 可读、sleep 前进（替代真实 time.sleep）。"""

    def __init__(self, t=1000.0):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _loop(clock, **kw):
    from src.strategy_framework.runtime.loop import EngineLoop
    return EngineLoop(name="t", step=5.0, now=clock.now, sleeper=clock.sleep, **kw)


class TestCadence:
    def test_period_fires_on_deadline(self):
        """period=30 在 t0/30/60/90 触发（到期驱动，非相位耦合）。"""
        clock = FakeClock()
        loop = _loop(clock)
        calls = []
        loop.every("p30", 30, lambda: calls.append(clock.t))
        loop.run(stop_after_iterations=20)
        assert len(calls) == 4
        assert calls[0] == 1000.0 and calls[1] == 1030.0 and calls[2] == 1060.0 and calls[3] == 1090.0

    def test_zero_period_every_step(self):
        """period=0 每步执行。"""
        clock = FakeClock()
        loop = _loop(clock)
        n = []
        loop.every("each", 0, lambda: n.append(1))
        loop.run(stop_after_iterations=7)
        assert len(n) == 7

    def test_independent_periods_no_phase_coupling(self):
        """7s/3s 两钩子各自按周期触发——counter%N 相位耦合消灭的证据。"""
        clock = FakeClock()
        loop = _loop(clock)
        a, b = [], []
        loop.every("a", 7, lambda: a.append(clock.t))
        loop.every("b", 3, lambda: b.append(clock.t))
        while clock.t < 1000.0 + 42:   # 手动驱动 42s
            clock.sleep(loop._next_wait())
            loop._preflight()
            loop._dispatch()
        assert [round(x - 1000) for x in a] == [0, 7, 14, 21, 28, 35, 42]
        assert [round(x - 1000) for x in b] == [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42]

    def test_wait_capped_by_step(self):
        """远期钩子的等待被 step 封顶；近期钩子精确到自己的周期（首航即触发是设计行为）。"""
        clock = FakeClock()
        loop = _loop(clock)
        loop.every("far", 100, lambda: None)
        loop._dispatch()                      # 首航立即触发
        assert loop._next_wait() == 5.0
        clock2 = FakeClock()
        loop2 = _loop(clock2)
        loop2.every("near", 3, lambda: None)
        loop2._dispatch()
        assert loop2._next_wait() == 3.0


class TestFailurePolicy:
    def test_failure_log_continues(self):
        """failure=log：钩子抛错不影响自身后续与其他钩子。"""
        clock = FakeClock()
        loop = _loop(clock)
        ok = []
        loop.every("bad", 0, lambda: (_ for _ in ()).throw(RuntimeError("x")))
        loop.every("good", 0, lambda: ok.append(1))
        loop.run(stop_after_iterations=5)
        assert len(ok) == 5

    def test_failure_exit_terminates(self):
        """failure=exit：钩子抛错走 os._exit（进程域退出语义；mock 不真退故断首次调用）。"""
        clock = FakeClock()
        loop = _loop(clock, fatal_exit_code=7)
        loop.every("fatal", 0, lambda: (_ for _ in ()).throw(RuntimeError("boom")), failure="exit")
        with patch("src.strategy_framework.runtime.loop.os._exit") as ex:
            loop.run(stop_after_iterations=3)
        assert ex.call_args_list[0].args == (7,)

    def test_duplicate_hook_name_rejected(self):
        clock = FakeClock()
        loop = _loop(clock)
        loop.every("x", 1, lambda: None)
        try:
            loop.every("x", 2, lambda: None)
            assert False, "应拒绝重名"
        except ValueError:
            pass


class TestPreflight:
    def test_watchdog_each_iteration(self):
        clock = FakeClock()
        wd = MagicMock()
        loop = _loop(clock, watchdog=wd)
        loop.every("n", 0, lambda: None)
        loop.run(stop_after_iterations=4)
        assert wd.call_count == 4

    def test_event_thread_dead_exits(self):
        """事件线程死亡 → os._exit（F-26 单一实现）。"""
        clock = FakeClock()
        ee = MagicMock()
        ee._thread.is_alive.return_value = False
        loop = _loop(clock, event_engines=(ee,))
        loop.every("n", 0, lambda: None)
        with patch("src.strategy_framework.runtime.loop.os._exit") as ex:
            loop.run(stop_after_iterations=2)
        assert ex.call_args_list[0].args == (1,)


class TestOnFatal:
    def test_event_thread_death_alerts_before_exit(self):
        """双盲审 P1：Restart=always 下 OnFailure 不触发——on_fatal 必须先告警再退。"""
        clock = FakeClock()
        ee = MagicMock()
        ee._thread.is_alive.return_value = False
        calls = []
        loop = _loop(clock, event_engines=(ee,), on_fatal=calls.append)
        loop.every("n", 0, lambda: None)
        with patch("src.strategy_framework.runtime.loop.os._exit") as ex:
            loop.run(stop_after_iterations=2)
        assert calls and calls[0] == "EventEngine 事件线程已死亡"   # 告警先行（mock 不真退故可重复）
        assert ex.call_args_list[0].args == (1,)

    def test_hook_exit_fatal_alerts(self):
        clock = FakeClock()
        calls = []
        loop = _loop(clock, on_fatal=calls.append)
        loop.every("f", 0, lambda: (_ for _ in ()).throw(RuntimeError("x")), failure="exit")
        with patch("src.strategy_framework.runtime.loop.os._exit"):
            loop.run(stop_after_iterations=1)
        assert len(calls) == 1 and "f" in calls[0]
