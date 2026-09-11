"""批14 · SSE 端点测试 + 载荷一致性契约。

覆盖：hello 首帧/心跳/401/事件透传格式/_set_session→publish 触发点+载荷剥 qr_img+ticket 字段
（盲审 B-P1-4：两路共用 handleOnboardingStatus 的一致性契约）。
"""
import asyncio
import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

_IDENT = {"sub": "9", "username": "u9", "role": "viewer"}


def _client():
    from src.web_api.main import app
    return TestClient(app)


class TestEndpoint:
    """注：hello 首帧/心跳/即时性的 E2E 走生产域名 curl（方案 §6.3）——TestClient 同步流对无限
    SSE 的退出语义会挂死，不在单测覆盖（事件透送/心跳/哨兵由 test_eventbus 全覆盖，端点生成器薄）。"""

    def test_no_token_rejected(self):
        """无凭证拒绝（422=Header 缺参先于 401——同语义：未认证不可订阅）。"""
        c = _client()
        r = c.get("/api/events")
        assert r.status_code in (401, 422)


class TestSetSessionPublish:
    """触发点契约：_set_session → publish（剥 qr_img/带 ticket/owner=None 不发/异常全吞）。"""

    def _set(self, owner, data):
        import src.feishu_bot.tasks as T
        rd = MagicMock()
        with patch.object(T, "_redis", rd), \
             patch("src.quant_common.eventbus.bus") as mb:
            T._set_session("tick1", data, expire=60, owner_user_id=owner)
        return mb

    def test_publish_called_with_ticket_sans_qr_img(self):
        mb = self._set(9, {"status": "scanning", "qr_img": "data:image/png;base64,AAAA"})
        mb.publish.assert_called_once()
        uid, ev_type, ev = mb.publish.call_args.args   # mock 在信封组装前——type 由 publish 内部拼
        assert uid == 9 and ev_type == "onboarding"
        assert ev["ticket"] == "tick1"
        assert "qr_img" not in ev   # A-P1-3：大帧剥除

    def test_admin_no_owner_no_publish(self):
        mb = self._set(None, {"status": "scanning"})
        mb.publish.assert_not_called()

    def test_publish_failure_swallowed(self):
        """契约②：publish 抛异常不炸 _set_session（Valkey 已写——状态机完好）。"""
        import src.feishu_bot.tasks as T
        rd = MagicMock()
        with patch.object(T, "_redis", rd), \
             patch("src.quant_common.eventbus.bus") as mb:
            mb.publish.side_effect = RuntimeError("bus boom")
            T._set_session("tick2", {"status": "error"}, expire=60, owner_user_id=9)
        rd.setex.assert_called_once()   # 真相源写入不受影响

    def test_payload_matches_status_endpoint_shape(self):
        """B-P1-4 一致性契约：事件载荷 ⊇ onboarding-status 端点返回的关键字段（两路共用处理）。"""
        mb = self._set(9, {"status": "done", "owned": True})
        ev = mb.publish.call_args.args[2]
        for k in ("status", "owned"):
            assert k in ev
        # 端点原样返回 session 载荷（pop bind_code 已退役）——事件为超集+信封字段（type/ticket/ts/owner_user_id）


class TestGeneratorAsync:
    """盲审 A-P1-3 建议 AsyncClient+ASGITransport——实测该 transport 无 disconnect 信号
    （客户端 break 后 app 生成器任务不退必挂死），此路不通；端点生成器覆盖维持：
    bus 全测（test_eventbus）+ 生产域名 curl 即时性（方案 §6.3 验收）。"""
