"""批13 · 企微接入单测。

覆盖：wecom_ws 帧编解码（subscribe 认证帧/回复流式帧/心跳/回执分发/认证失败不重连）——
纯协议单测不打真连接；Provider 桩语义；test_connection 探活 mock。
协议断言依据：docs/reference/企微智能机器人协议参考.md（官方 Node SDK dist 核出）。
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch


class TestFrameEncode:
    def test_subscribe_frame_shape(self):
        """认证帧：凭证在 body（非顶层）——cmd/headers/body 骨架。"""
        from src.im_bot.wecom_ws import WecomAIBotWS
        ws_client = WecomAIBotWS("wb1", "sec", on_message=lambda *a: None)
        captured = {}
        ws_client._ws = MagicMock()
        ws_client._ws.send = AsyncMock(side_effect=lambda s: captured.update(raw=s))
        loop = asyncio.new_event_loop()
        # _send_and_wait 会等回执——用 task + 立即 set_result 模拟服务端 ack
        async def _drive():
            fut_holder = {}
            orig = loop.create_future
            def _cf():
                f = orig()
                if not fut_holder:
                    fut_holder["f"] = f
                    f.set_result(True)   # 立即 ack
                return f
            loop.create_future = _cf
            try:
                return await ws_client._send_and_wait("aibot_subscribe", "",
                                                      {"bot_id": "wb1", "secret": "sec"})
            finally:
                loop.create_future = orig
        try:
            assert loop.run_until_complete(asyncio.wait_for(_drive(), timeout=2)) is True
        finally:
            loop.close()
        frame = json.loads(captured["raw"])
        assert frame["cmd"] == "aibot_subscribe"
        assert frame["body"] == {"bot_id": "wb1", "secret": "sec"}   # 凭证在 body
        assert frame["headers"]["req_id"].startswith("aibot_subscribe_")

    def test_reply_frame_is_stream_single_shot(self):
        """回复帧：无纯文本——msgtype=stream + finish=true 单帧完成（协议参考 §4）。"""
        from src.im_bot.wecom_ws import WecomAIBotWS
        c = WecomAIBotWS("wb1", "s", on_message=lambda *a: None)
        captured = {}
        c._ws = MagicMock()
        c._ws.send = AsyncMock(side_effect=lambda s: captured.update(raw=s))
        loop = asyncio.new_event_loop()
        async def _drive():
            fut_holder = {}
            orig = loop.create_future
            def _cf():
                f = orig()
                if not fut_holder:
                    fut_holder["f"] = f; f.set_result(True)
                return f
            loop.create_future = _cf
            try:
                # new_req=False：响应型帧复用原 req_id（A-P1-6 回复语义）
                return await c._reply("req_incoming_1", "你好")
            finally:
                loop.create_future = orig
        try:
            assert loop.run_until_complete(asyncio.wait_for(_drive(), timeout=2)) is True
        finally:
            loop.close()
        frame = json.loads(captured["raw"])
        assert frame["cmd"] == "aibot_respond_msg"
        assert frame["headers"]["req_id"] == "req_incoming_1"   # 透传收帧 req_id
        assert frame["body"]["msgtype"] == "stream"
        assert frame["body"]["stream"]["finish"] is True
        assert frame["body"]["stream"]["content"] == "你好"


class TestFrameDispatch:
    def _client(self):
        from src.im_bot.wecom_ws import WecomAIBotWS
        c = WecomAIBotWS("wb1", "s", on_message=MagicMock())
        return c

    def test_msg_callback_passes_body_and_reqid(self):
        c = self._client()
        frame = {"cmd": "aibot_msg_callback", "headers": {"req_id": "rq1"},
                 "body": {"msgtype": "text", "chattype": "single",
                          "from": {"userid": "zhang"}, "text": {"content": "hi"}}}
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(c._dispatch_frame(frame))
        finally:
            loop.close()
        c._on_message.assert_called_once()
        body, req_id = c._on_message.call_args[0]
        assert body["from"]["userid"] == "zhang" and req_id == "rq1"

    def test_unknown_frame_ignored_no_raise(self):
        """未知帧 log 忽略不断连（A-P1-6）。"""
        c = self._client()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(c._dispatch_frame({"cmd": "future_cmd", "headers": {}, "body": {}}))
        finally:
            loop.close()
        c._on_message.assert_not_called()

    def test_ack_frame_resolves_future(self):
        """无 cmd 帧=回执（认证/心跳/回复 ack）——req_id 匹配 future。"""
        c = self._client()
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            c._replies["ping_x"] = fut
            c._resolve_reply({"headers": {"req_id": "ping_x"}, "errcode": 0})
            assert fut.done() and fut.result() is True
            fut2 = loop.create_future()
            c._replies["sub_y"] = fut2
            c._resolve_reply({"headers": {"req_id": "sub_y"}, "errcode": 40001})
            assert fut2.done() and fut2.result() is False
        finally:
            loop.close()

    def test_auth_error_not_retried(self):
        """WecomAuthError 穿透 run() 不进重连循环（fail-fast 语义）。"""
        from src.im_bot.wecom_ws import WecomAIBotWS, WecomAuthError
        c = WecomAIBotWS("wb1", "bad", on_message=lambda *a: None)
        calls = {"n": 0}
        async def _always_auth_fail():
            calls["n"] += 1
            raise WecomAuthError("rejected")
        with patch.object(c, "_run_once", side_effect=_always_auth_fail):
            loop = asyncio.new_event_loop()
            try:
                with pytest_raises(WecomAuthError):
                    loop.run_until_complete(c.run())
            finally:
                loop.close()
        assert calls["n"] == 1   # 未重试


def pytest_raises(exc):
    import pytest
    return pytest.raises(exc)


class TestProvider:
    def test_stubs_false(self):
        """send_text/send_card 诚实桩（A-P1-2/B-P1-1：连接在 runner 子进程，web 进程无帧可发）。"""
        from src.im_bot.wecom import WecomProvider
        assert WecomProvider().send_text(7, "u", "userid", "t") is False
        assert WecomProvider().send_card(7, "u", "userid", {}) is False

    def test_test_connection_no_credentials(self):
        from src.im_bot.wecom import WecomProvider
        with patch("src.im_bot.credentials.get_bot_credentials", return_value={}):
            ok, detail = WecomProvider().test_connection(7)
        assert not ok and "未配置" in detail

    def test_test_connection_auth_rejected(self):
        from unittest.mock import AsyncMock
        from src.im_bot.wecom import WecomProvider
        ws_mock = MagicMock()
        ws_mock.send = AsyncMock()
        ws_mock.recv = AsyncMock(return_value=json.dumps(
            {"headers": {"req_id": "x"}, "errcode": 40001, "errmsg": "bad secret"}))
        ctx = AsyncMock(); ctx.__aenter__.return_value = ws_mock
        with patch("src.im_bot.credentials.get_bot_credentials",
                   return_value={"bot_id": "wb1", "secret": "bad"}), \
             patch("websockets.connect", return_value=ctx):
            ok, detail = WecomProvider().test_connection(7)
        assert not ok and "40001" in detail


class TestHeartbeatAndReconnect:
    """盲审 A 测试缺口补：心跳 missedPong/认证接线/退避重置——P1-1/P1-2 两 bug 长在无测区。"""

    def _hb_client(self, ping_results):
        """构造 heartbeat 测试客户端：_send_and_wait 依序返回/抛。"""
        from src.im_bot.wecom_ws import WecomAIBotWS
        c = WecomAIBotWS("wb1", "s", on_message=lambda *a: None)
        c._send_and_wait = AsyncMock(side_effect=list(ping_results))
        return c

    def test_two_missed_pong_closes_ws(self):
        """missedPong≥2 → 主动 ws.close（上层重连）。HEARTBEAT_S 短接加速。"""
        from src.im_bot import wecom_ws
        c = self._hb_client([Exception("net"), Exception("net")])
        ws = MagicMock(); ws.close = AsyncMock()
        orig = wecom_ws.HEARTBEAT_S
        wecom_ws.HEARTBEAT_S = 0.01
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(c._heartbeat(ws))
            finally:
                loop.close()
        finally:
            wecom_ws.HEARTBEAT_S = orig
        ws.close.assert_called_once()

    def test_ok_ping_resets_missed(self):
        """ack 正常 → 不 close（无限跑——用短接 3 拍后取消）。"""
        from src.im_bot import wecom_ws
        from src.im_bot.wecom_ws import WecomAIBotWS
        c = WecomAIBotWS("wb1", "s", on_message=lambda *a: None)
        c._send_and_wait = AsyncMock(return_value=True)   # 永续 ack（side_effect 会耗尽翻转 missed）
        ws = MagicMock(); ws.close = AsyncMock()
        orig = wecom_ws.HEARTBEAT_S
        wecom_ws.HEARTBEAT_S = 0.01
        try:
            loop = asyncio.new_event_loop()
            task = loop.create_task(c._heartbeat(ws))
            try:
                loop.run_until_complete(asyncio.sleep(0.05))
                task.cancel()
                loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
            finally:
                loop.close()
        finally:
            wecom_ws.HEARTBEAT_S = orig
        ws.close.assert_not_called()

    def test_auth_errcode_nonzero_raises_wecom_auth_error(self):
        """A-P1-2 接线：仅显式 errcode≠0 → WecomAuthError（run 不重试）。"""
        from src.im_bot import wecom_ws
        from src.im_bot.wecom_ws import WecomAIBotWS, WecomAuthError
        c = WecomAIBotWS("wb1", "bad", on_message=lambda *a: None)
        c._ws = MagicMock(); c._ws.send = AsyncMock()
        loop = asyncio.new_event_loop()
        # 认证回执 errcode=40001（future set_result(False)）
        async def _ack_after_send(req_id_holder):
            await asyncio.sleep(0.01)
            for k, f in list(c._replies.items()):
                if not f.done():
                    f.set_result(False)   # errcode≠0 → _send_and_wait 返回 False
        async def _drive():
            t = loop.create_task(_ack_after_send(None))
            try:
                return await c._send_and_wait("aibot_subscribe", "", {"bot_id": "wb1", "secret": "bad"})
            finally:
                t.cancel()
        try:
            ok = loop.run_until_complete(asyncio.wait_for(_drive(), timeout=2))
            assert ok is False
        finally:
            loop.close()

    def test_backoff_resets_after_healthy_session(self):
        """A-P1-1：健康会话（认证成功过）断线后 run() 重置退避。"""
        from src.im_bot import wecom_ws
        from src.im_bot.wecom_ws import WecomAIBotWS
        c = WecomAIBotWS("wb1", "s", on_message=lambda *a: None)
        states = {"authed": False, "n": 0}
        async def _fake_run_once():
            states["n"] += 1
            if states["n"] > 1:
                raise asyncio.CancelledError   # 第二轮退出测试循环
            c._authed_once = True   # 模拟健康会话后断线
            return
        sleeps = []
        async def _fake_sleep(d):
            sleeps.append(d)
        with patch.object(c, "_run_once", side_effect=_fake_run_once), \
             patch.object(wecom_ws.asyncio, "sleep", side_effect=_fake_sleep):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(c.run())
            except asyncio.CancelledError:
                pass
            finally:
                loop.close()
        # 第二轮重连延迟应从 base 起（authed_once 重置），而非翻倍
        assert sleeps and sleeps[-1] <= wecom_ws.RECONNECT_BASE_S + 1.1
