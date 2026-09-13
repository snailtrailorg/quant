"""批18 · 通知中心 SSE 实时化单测（Valkey 跨进程桥）。

覆盖（方案 v2 双盲审 A/B 吸收后的契约）：
1. publish_cross_process：频道路由（all/u:{uid}）/payload 只带 type（可见性）/永不 raise
   （含 redis 异常态）/短连接关闭
2. 桥 handle_message：all→publish_all / u:{uid}→publish / 毒消息（畸形 JSON/非数字 uid）不断流
3. notify 生产者：insert 成功广播「信号帧」/去重命中不广播（行为分叉点，B-P2-6）
"""
import asyncio
from unittest.mock import MagicMock, patch

from src.quant_common.eventbus import EventBus, SSE_CHANNEL_ALL, SSE_CHANNEL_USER


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeRedis:
    """publish 记录器（pubsub 不在此用——桥主体经 handle_message 直测）。"""
    calls = []

    def publish(self, channel, payload):
        _FakeRedis.calls.append((channel, payload))

    def close(self):
        pass


class TestPublishCrossProcess:
    def setup_method(self):
        _FakeRedis.calls = []

    def _bus(self):
        return EventBus()

    def test_broadcast_channel_and_payload(self):
        bus = self._bus()
        fake = _FakeRedis()
        with patch("src.quant_common.eventbus.redis") as rmod:
            rmod.Redis.from_url.return_value = fake
            bus.publish_cross_process(0, "notification", {})
        ch, payload = _FakeRedis.calls[0]
        assert ch == SSE_CHANNEL_ALL
        assert '"notification"' in payload   # {"type": "notification"} —— 信号帧零内容（可见性矩阵）

    def test_user_channel_routing(self):
        bus = self._bus()
        with patch("src.quant_common.eventbus.redis") as rmod:
            rmod.Redis.from_url.return_value = _FakeRedis()
            bus.publish_cross_process(42, "im", {"x": 1})
        assert _FakeRedis.calls[0][0] == SSE_CHANNEL_USER.format(uid=42)

    def test_never_raises_on_redis_failure(self):
        """redis 异常永不 raise（调用方=实盘告警路径；轮询兜底覆盖）。"""
        bus = self._bus()
        with patch("src.quant_common.eventbus.redis") as rmod:
            rmod.Redis.from_url.side_effect = ConnectionError("valkey down")
            bus.publish_cross_process(0, "notification", {})   # 不炸即过

    def test_timeout_params_present(self):
        """盲审A-P0-2：连接必须带双 1s 超时（Valkey hung 防挂死）。"""
        bus = self._bus()
        with patch("src.quant_common.eventbus.redis") as rmod:
            rmod.Redis.from_url.return_value = _FakeRedis()
            bus.publish_cross_process(0, "notification", {})
        kwargs = rmod.Redis.from_url.call_args.kwargs
        assert kwargs.get("socket_connect_timeout") == 1 and kwargs.get("socket_timeout") == 1


class TestSseBridgeHandle:
    def test_all_channel_fans_to_every_watcher(self):
        from src.quant_common.sse_bridge import SseBridge
        bus = EventBus()
        bridge = SseBridge(bus)
        async def _drive():
            q1, q2 = bus.subscribe(1), bus.subscribe(2)
            bridge.handle_message("quant:sse:all", '{"type": "notification"}')
            for q in (q1, q2):
                item = await asyncio.wait_for(q.get(), timeout=2)
                assert item["type"] == "notification"
        _run(_drive())

    def test_user_channel_targets_uid(self):
        from src.quant_common.sse_bridge import SseBridge
        bus = EventBus()
        bridge = SseBridge(bus)
        async def _drive():
            q = bus.subscribe(7)
            bridge.handle_message("quant:sse:u:7", '{"type": "im", "k": "v"}')
            item = await asyncio.wait_for(q.get(), timeout=2)
            assert item["type"] == "im" and item["k"] == "v"
        _run(_drive())

    def test_poison_messages_isolated(self):
        """毒消息（畸形 JSON/非数字 uid/非 dict）吞掉不断流——后续好帧照常达。"""
        from src.quant_common.sse_bridge import SseBridge
        bus = EventBus()
        bridge = SseBridge(bus)
        async def _drive():
            q = bus.subscribe(7)
            bridge.handle_message("quant:sse:all", "not-json{")
            bridge.handle_message("quant:sse:u:abc", '{"type": "x"}')
            bridge.handle_message("quant:sse:all", '[1,2,3]')
            bridge.handle_message("quant:sse:all", '{"type": "notification"}')
            item = await asyncio.wait_for(q.get(), timeout=2)
            assert item["type"] == "notification"
        _run(_drive())


class TestNotifyBroadcasts:
    def _mod(self):
        # alert_notify/__init__ 重导出 notify 函数遮蔽同名子模块——importlib 拿真模块对象
        import importlib
        return importlib.import_module("src.alert_notify.notify")

    def test_insert_success_publishes_signal_frame(self):
        N = self._mod()
        rk = MagicMock()
        rk.exists.return_value = 0   # 未命中去重
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = [123]
        with patch.object(N, "_redis", return_value=rk), \
             patch("src.data_platform.db.get_conn", return_value=conn), \
             patch("src.quant_common.eventbus.bus") as bus:
            N.notify("warn", "risk", "t", "b")
            bus.publish_cross_process.assert_called_once_with(0, "notification", {})

    def test_dedup_hit_does_not_publish(self):
        """去重命中（return None）不广播——与铃铛数据一致性对齐（B-P2-6）。"""
        N = self._mod()
        rk = MagicMock()
        rk.exists.return_value = 1   # 去重键已存在
        with patch.object(N, "_redis", return_value=rk), \
             patch("src.data_platform.db.get_conn") as gc, \
             patch("src.quant_common.eventbus.bus") as bus:
            assert N.notify("warn", "risk", "t", "b") is None
            gc.execute.assert_not_called()
            bus.publish_cross_process.assert_not_called()
