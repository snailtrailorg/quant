"""SubscriptionManager 测试（批 2）——diff 增删 / 重放先退 / 重连沿，零真实 IO。

语义锁：hub _sync_subscriptions（491-515 行）收编进骨架后的行为等价——
diff 分支先加后退、replay 分支先退 removed 再全量订阅、重连沿强制重放。
"""
from unittest.mock import MagicMock

from src.strategy_framework.runtime.subs import SubscriptionManager


def _mgr(want, calls, sub_exc_syms=()):
    """记录型后端：subscribe/unsubscribe 记序；指定标的 subscribe 抛错（隔离测试）。"""
    def subscribe(s):
        calls.append(("sub", s))
        if s in sub_exc_syms:
            raise RuntimeError(f"sub boom {s}")

    def unsubscribe(s):
        calls.append(("unsub", s))

    return SubscriptionManager(lambda: want, subscribe, unsubscribe)


class TestPoll:
    def test_first_poll_subscribes_all(self):
        calls = []
        m = _mgr({"A.SSE", "B.SSE"}, calls)
        m.poll()
        assert [c for c in calls if c[0] == "sub"] == [("sub", "A.SSE"), ("sub", "B.SSE")]
        assert m.current == {"A.SSE", "B.SSE"}

    def test_diff_add_and_remove_added_first(self):
        """diff 分支：先加后退（hub 原序 508-513）。"""
        calls = []
        m = _mgr({"A", "B"}, calls)
        m.poll()
        calls.clear()
        m._desired = lambda: {"B", "C"}   # A 退、C 进
        m.poll()
        assert calls == [("sub", "C"), ("unsub", "A")]
        assert m.current == {"B", "C"}

    def test_no_change_no_calls(self):
        calls = []
        m = _mgr({"A"}, calls)
        m.poll()
        calls.clear()
        m.poll()
        assert calls == []

    def test_single_symbol_failure_isolated(self):
        """单标的订阅抛错只记日志：其余标的照常，不上抛（幂等重放兜底）。"""
        calls = []
        m = _mgr({"BAD", "OK"}, calls, sub_exc_syms={"BAD"})
        m.poll()                                  # 不抛即通过
        assert calls == [("sub", "BAD"), ("sub", "OK")]   # BAD 失败不阻断 OK

    def test_desired_failure_keeps_old_set(self):
        """真相源读失败沿用旧集（hub「读订阅真相源失败」语义），本轮零动作。"""
        calls = []
        m = _mgr({"A"}, calls)
        m.poll()
        calls.clear()
        m._desired = MagicMock(side_effect=RuntimeError("db down"))
        m.poll()                                  # 不抛
        assert calls == []
        assert m.current == {"A"}


class TestReplay:
    def test_replay_unsubscribes_removed_first(self):
        """replay 分支：先退 removed 再全量订阅（补盲审 S1——窗口内移除不泄漏）。"""
        calls = []
        m = _mgr({"A", "B"}, calls)
        m.poll()
        calls.clear()
        m._desired = lambda: {"B", "C"}
        m.replay()
        assert calls[0] == ("unsub", "A")         # 退订先于一切订阅
        assert [("sub", "B"), ("sub", "C")] == [c for c in calls if c[0] == "sub"]
        assert m.current == {"B", "C"}

    def test_replay_resubscribes_unchanged_set(self):
        """幂等全量：集合未变也重放全部订阅（XTP 重连不恢复订阅的双兜底）。"""
        calls = []
        m = _mgr({"A", "B"}, calls)
        m.poll()
        calls.clear()
        m.replay()
        assert calls == [("sub", "A"), ("sub", "B")]   # 无退订（removed 空集）

    def test_on_reconnect_edge_forces_replay(self):
        """重连沿 = 强制全量重放（先退 removed）。"""
        calls = []
        m = _mgr({"A", "B"}, calls)
        m.poll()
        calls.clear()
        m._desired = lambda: {"B", "C"}
        m.on_reconnect_edge()
        assert calls[0] == ("unsub", "A")
        assert {c[1] for c in calls if c[0] == "sub"} == {"B", "C"}


class TestCurrent:
    def test_current_returns_copy(self):
        """current 是拷贝：外部改动不渗入内部状态。"""
        m = _mgr(set(), [])
        cur = m.current
        cur.add("X")
        assert m.current == set()
