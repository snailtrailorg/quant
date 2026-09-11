"""批14 · SSE 事件总线单测（quant_common/eventbus）。

覆盖（方案 v2 盲审 P1-4 测试缺口）：跨线程投递/loop 未锚定与已关闭 no-op/publish 永不 raise
（_set_session 契约②）/queue 满丢最旧/同 uid 多 Queue 并行/close 哨兵。
"""
import asyncio
import threading
from unittest.mock import patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestBusBasics:
    def test_subscribe_publish_deliver(self):
        from src.quant_common.eventbus import EventBus
        bus = EventBus()
        async def _drive():
            q = bus.subscribe(9)
            bus.publish(9, "onboarding", {"status": "scanning", "ticket": "t1"})
            item = await asyncio.wait_for(q.get(), timeout=2)
            assert item["type"] == "onboarding" and item["ticket"] == "t1"
            bus.unsubscribe(9, q)
            assert not bus._watchers.get(9)
        _run(_drive())

    def test_publish_before_any_subscriber_noop(self):
        """loop 未锚定：publish 一律 no-op（不炸不缓存）。"""
        from src.quant_common.eventbus import EventBus
        bus = EventBus()
        bus.publish(9, "x", {})   # 无 subscribe 过——loop 未锚定
        assert bus._loop is None

    def test_publish_after_loop_closed_noop(self):
        from src.quant_common.eventbus import EventBus
        bus = EventBus()
        async def _drive():
            q = bus.subscribe(9)
            return q
        q = _run(_drive())   # loop 已关（_run 里 close）
        bus.publish(9, "x", {})   # loop closed——no-op 不 raise

    def test_publish_never_raises(self):
        """契约②：publish 故障绝不炸调用方（_set_session 状态机保护）。"""
        from src.quant_common.eventbus import EventBus
        bus = EventBus()
        with patch.object(bus, "_dispatch", side_effect=RuntimeError("boom")):
            bus.publish(9, "x", {})   # 不 raise
            bus.close()

    def test_cross_thread_publish(self):
        """onboarding daemon 线程（sync）publish → loop 协程收到。"""
        from src.quant_common.eventbus import EventBus
        bus = EventBus()
        got = []
        async def _drive():
            q = bus.subscribe(9)
            t = threading.Thread(target=lambda: bus.publish(9, "onboarding", {"status": "done"}))
            t.start()
            item = await asyncio.wait_for(q.get(), timeout=2)
            got.append(item)
            t.join()
            bus.unsubscribe(9, q)
        _run(_drive())
        assert got and got[0]["status"] == "done"

    def test_multi_queue_same_uid_and_unsubscribe_zero(self):
        """同 uid 双 watcher（多标签页）都收；逐个退订归零。"""
        from src.quant_common.eventbus import EventBus
        bus = EventBus()
        async def _drive():
            q1, q2 = bus.subscribe(9), bus.subscribe(9)
            assert len(bus._watchers[9]) == 2
            bus.publish(9, "x", {"a": 1})
            i1 = await asyncio.wait_for(q1.get(), timeout=2)
            i2 = await asyncio.wait_for(q2.get(), timeout=2)
            assert i1 == i2 == {"type": "x", "a": 1}
            bus.unsubscribe(9, q1)
            assert len(bus._watchers[9]) == 1
            bus.unsubscribe(9, q2)
            assert 9 not in bus._watchers
        _run(_drive())

    def test_queue_full_drops_oldest(self):
        from src.quant_common.eventbus import EventBus, QUEUE_MAX
        bus = EventBus()
        async def _drive():
            q = bus.subscribe(9)
            for i in range(QUEUE_MAX + 5):
                bus.publish(9, "n", {"i": i})
            await asyncio.sleep(0.05)   # 让 call_soon_threadsafe 排空
            items = []
            while not q.empty():
                items.append(q.get_nowait())
            assert len(items) == QUEUE_MAX
            assert items[-1]["i"] == QUEUE_MAX + 4   # 最新的在，最旧的被丢
            bus.unsubscribe(9, q)
        _run(_drive())

    def test_close_sends_sentinel_to_all(self):
        """停机哨兵：全部 watcher 收 None（SSE 生成器收即退）。"""
        from src.quant_common.eventbus import EventBus, CLOSE_SENTINEL
        bus = EventBus()
        async def _drive():
            q1, q2 = bus.subscribe(1), bus.subscribe(2)
            bus.close()
            s1 = await asyncio.wait_for(q1.get(), timeout=2)
            s2 = await asyncio.wait_for(q2.get(), timeout=2)
            assert s1 is CLOSE_SENTINEL and s2 is CLOSE_SENTINEL
        _run(_drive())

    def test_other_uid_isolated(self):
        from src.quant_common.eventbus import EventBus
        bus = EventBus()
        async def _drive():
            q9 = bus.subscribe(9)
            bus.publish(1, "x", {})   # 发给 uid=1
            bus.publish(9, "y", {})
            item = await asyncio.wait_for(q9.get(), timeout=2)
            assert item["type"] == "y"
        _run(_drive())
