"""XReadSleeper 契约测试（批 4b）——never-raise 矩阵 / NOGROUP 75 / 节奏数学 / 单线程契约。

规格：docs/任务/批4-worker迁移与trading解耦.md v2.1「XReadSleeper 规格」节——
block=min(500, 距下钩子剩余毫秒)/全异常边界不外抛（含 on_batch）/Timeout 静默其他吞后睡
1s 返回/NOGROUP os._exit(75)（禁 sys.exit）/单线程禁后台线程。
"""
import threading
from unittest.mock import patch

from src.strategy_framework.runtime.xsleeper import (
    BLOCK_CAP_MS,
    NOGROUP_EXIT_CODE,
    RETRY_SLEEP_S,
    XReadSleeper,
)


class RedisTimeout(Exception):
    """redis.exceptions.TimeoutError 同名类（按类名归类，与旧 worker 判定同款）。"""


class _Redis:
    """xreadgroup 可编程 fake：batch 返回值 / 抛错 / 调用记录。"""

    def __init__(self, batch=None, exc=None):
        self.batch = batch
        self.exc = exc
        self.calls = []

    def xreadgroup(self, group, consumer, streams, count=10, block=0):
        self.calls.append({"group": group, "consumer": consumer, "streams": streams,
                           "count": count, "block": block})
        if self.exc is not None:
            raise self.exc
        return self.batch


def _sleeper(r=None, on_batch=None):
    return XReadSleeper(r if r is not None else _Redis(), "hub:bars:X.SHSE", "task-1",
                        "w-1", on_batch or (lambda b: None))


class TestBlockMath:
    """节奏数学：block=min(500, 距下钩子剩余毫秒)——定时钩子不被繁忙流饿死。"""

    def test_block_capped_at_500ms(self):
        r = _Redis(batch=[])
        _sleeper(r)(5.0)   # 下钩子 5s 远：block 封顶 500ms
        assert r.calls[-1]["block"] == 500
        assert r.calls[-1]["block"] == BLOCK_CAP_MS

    def test_block_follows_short_wait(self):
        r = _Redis(batch=[])
        _sleeper(r)(0.25)
        assert r.calls[-1]["block"] == 250

    def test_zero_and_negative_wait_clamped_to_1ms(self):
        """双盲 B P1：剩余≤0 时钳 1ms——禁 BLOCK 0（Redis 协议=永久阻塞，
        逃逸靠 socket_timeout=3 巧合不可依赖）。原断言 [0,0] 系反向锁定，翻转。"""
        r = _Redis(batch=[])
        _sleeper(r)(0.0)
        _sleeper(r)(-1.0)
        assert [c["block"] for c in r.calls] == [1, 1]

    def test_read_params(self):
        """组/消费者/流 '>' / count 与 worker 流消费参数一致（R-BR6 语义）。"""
        r = _Redis(batch=[])
        _sleeper(r)(5.0)
        c = r.calls[-1]
        assert c["group"] == "task-1" and c["consumer"] == "w-1"
        assert c["streams"] == {"hub:bars:X.SHSE": ">"} and c["count"] == 10


class TestDelivery:
    def test_batch_delivered_inline(self):
        got = []
        batch = [("hub:bars:X.SHSE", [("1-1", {"ts": "t"})])]
        s = _sleeper(_Redis(batch=batch), on_batch=got.append)
        s(5.0)
        assert got and got[0] is batch

    def test_empty_batch_no_callback(self):
        got = []
        s = _sleeper(_Redis(batch=[]), on_batch=got.append)
        s(5.0)
        assert got == []


class TestNeverRaise:
    """never-raise 矩阵（规格 P1 双同）：__call__ 边界全异常不外抛——异常传穿会命中
    调用方 finally 的 os._exit(0)=任务静默死（2026-08-20 A3 事故类）。"""

    def test_timeout_silent_no_sleep(self):
        """Timeout 类静默返回：不睡（BLOCK 超时是正常到期唤醒路径）。"""
        with patch("src.strategy_framework.runtime.xsleeper.time.sleep") as sl:
            _sleeper(_Redis(exc=RedisTimeout("timeout")))(5.0)
        sl.assert_not_called()

    def test_other_exception_swallows_sleeps_1s(self):
        """其他类吞后睡 1s 返回、下轮再试（禁无界内旋——不返会饿死心跳/停止钩子）。"""
        with patch("src.strategy_framework.runtime.xsleeper.time.sleep") as sl:
            _sleeper(_Redis(exc=ConnectionError("refused")))(5.0)   # 不抛即通过
        sl.assert_called_once_with(RETRY_SLEEP_S)

    def test_on_batch_exception_swallowed(self):
        """on_batch 批处理回调内异常同样穿边界静默死（规格明写含 on_batch）。"""
        with patch("src.strategy_framework.runtime.xsleeper.time.sleep") as sl:
            _sleeper(_Redis(batch=[("s", [("1-1", {})])]),
                     on_batch=lambda b: (_ for _ in ()).throw(ValueError("handler boom")))(5.0)
        sl.assert_called_once_with(RETRY_SLEEP_S)   # 非 Timeout 类：吞后睡 1s

    def test_on_batch_timeout_exception_silent(self):
        with patch("src.strategy_framework.runtime.xsleeper.time.sleep") as sl:
            _sleeper(_Redis(batch=[("s", [("1-1", {})])]),
                     on_batch=lambda b: (_ for _ in ()).throw(RedisTimeout("x")))(5.0)
        sl.assert_not_called()


class TestNogroupPath:
    """NOGROUP 处置（v2.1 复核双同修死）：直接 os._exit(75) 交 systemd 重启 → run() 启动段
    组重建接手——禁 sys.exit（SystemExit 传穿 finally 吞成 0=自设陷阱反噬）。"""

    def test_nogroup_exits_75(self):
        with patch("src.strategy_framework.runtime.xsleeper.os._exit") as ex, \
             patch("src.strategy_framework.runtime.xsleeper.time.sleep") as sl:
            _sleeper(_Redis(exc=Exception("NOGROUP No such key 'hub:bars:X' or group 'task-1'")))(5.0)
        ex.assert_called_once_with(NOGROUP_EXIT_CODE)
        assert NOGROUP_EXIT_CODE == 75
        sl.assert_not_called()   # NOGROUP 不走重试睡（进程已退出）

    def test_nogroup_matched_within_message(self):
        with patch("src.strategy_framework.runtime.xsleeper.os._exit") as ex:
            _sleeper(_Redis(exc=RuntimeError("BUSYGROUP consumer group ... NOGROUP ...")))(5.0)
        ex.assert_called_once_with(75)


class TestSingleThread:
    """单线程契约（规格写死）：on_batch 在调用线程内联执行，禁止后台线程。"""

    def test_on_batch_runs_in_caller_thread(self):
        seen = {}
        s = _sleeper(_Redis(batch=[("s", [("1-1", {})])]),
                     on_batch=lambda b: seen.setdefault("ident", threading.get_ident()))
        s(5.0)
        assert seen["ident"] == threading.get_ident()

    def test_module_has_no_threading(self):
        """模块源不 import threading（后台线程的结构性禁止——源断言锁，E-1 教训同款）。"""
        import src.strategy_framework.runtime.xsleeper as m
        assert not any("threading" in str(getattr(v, "__name__", ""))
                       for v in (m.time, m.os, m.logging))
        import inspect
        assert "import threading" not in inspect.getsource(m)
