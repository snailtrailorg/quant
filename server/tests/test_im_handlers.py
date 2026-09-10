"""批13 · 通用消息处理链单测（handlers.handle_incoming）。

覆盖：未绑定拒答（身份标识参数化）/读类问答/操作类降级（confirm_card=None 文本拒答——
A-P2-2 读类执行完再拒答）/confirm_card 回调发卡/caller 参数化（A-P2-1）/首见留痕。
"""
from unittest.mock import MagicMock, patch


def _identity(perms=None):
    return {"user_id": 1, "username": "u1", "role": "analyst", "perms": perms or {"read"}}


class TestUnbound:
    """五轮后：自有 bot 由 resolve owner 直通不拒答——本组测兜底分支（账号停用等罕见态）。"""

    def test_unbound_fallback_reply(self):
        from src.im_bot.handlers import handle_incoming
        from unittest.mock import MagicMock, patch
        conn = MagicMock(); conn.__enter__.return_value = conn
        with patch("src.data_platform.db.get_conn", return_value=conn), \
             patch("src.im_bot.users.resolve_im_identity", return_value=None):
            sent = []
            handle_incoming("dingtalk", 7, "staff_abc", "你好", sent.append, "p2p")
        assert sent and "已停用" in sent[0]


class _FakeResp:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class TestChatLoop:
    def _chat_ctx(self, responses, operational=()):
        """gateway patch：实例 .chat side_effect + **模块级**工具表（gateway 实例名与模块名
        撞车——READ_TOOLS 在模块上，字符串 patch 会落到实例上 AttributeError）+ 首见留痕。"""
        from importlib import import_module
        from src.llm_gateway import gateway as _gw_inst
        gm = import_module("src.llm_gateway.gateway")
        return [
            patch("src.im_bot.users.resolve_im_identity", return_value=_identity()),
            patch.object(_gw_inst, "chat", side_effect=list(responses)),   # chat=实例方法（test_feishu_integration 同款）
            patch.object(gm, "READ_TOOLS", []),                            # 工具表=模块级
            patch.object(gm, "OPERATIONAL_TOOLS", list(operational)),
        ]

    def test_read_reply_and_caller_param(self):
        """读类：无工具→回复；gateway.chat(caller=provider)（A-P2-1）。"""
        from contextlib import ExitStack
        from src.im_bot.handlers import handle_incoming
        sent = []
        with ExitStack() as es:
            ps = [es.enter_context(p) for p in self._chat_ctx([_FakeResp(content="风控正常")])]
            mchat = ps[1]   # _chat_ctx[1]=chat patcher（五轮后 _first_seen_note 项删除）
            handle_incoming("dingtalk", 7, "s1", "查风控", sent.append, "p2p")
        assert sent == ["风控正常"]
        assert mchat.call_args.kwargs.get("caller") == "dingtalk"

    def test_operational_degrades_without_confirm_card(self):
        """操作类降级（A-P2-2）：读类执行完后，操作类文本拒答（不发卡不执行）。"""
        from src.im_bot.handlers import handle_incoming
        tool_op = MagicMock(); tool_op.name = "emergency_halt"
        tool_rd = MagicMock(); tool_rd.name = "query_risk_state"
        sent = []
        for p in self._chat_ctx([_FakeResp(tool_calls=[
                {"id": "1", "name": "query_risk_state", "arguments": "{}"},
                {"id": "2", "name": "emergency_halt", "arguments": "{}"},
            ])], operational=[tool_op]):
            p.start()
        try:
            from src.im_bot.handlers import handle_incoming
            handle_incoming("wecom", 7, "u1", "停止所有策略", sent.append, "p2p", confirm_card=None)
        finally:
            import unittest.mock as _um
            _um.patch.stopall()
        assert len(sent) == 1 and "网页端" in sent[0]   # 文案师版降级文案；单条拒答，读类结果不追发（与飞书对齐）

    def test_operational_with_confirm_card_sends_card(self):
        """飞书路径：confirm_card 回调发卡（行为等价回归锚点）。"""
        from src.im_bot.handlers import handle_incoming
        tool_op = MagicMock(); tool_op.name = "emergency_halt"
        sent, cards = [], []
        for p in self._chat_ctx([_FakeResp(tool_calls=[
                {"id": "1", "name": "emergency_halt", "arguments": "{}"}])], operational=[tool_op]):
            p.start()
        try:
            from src.im_bot.handlers import handle_incoming
            handle_incoming("feishu", 7, "ou_x", "熔断", sent.append, "p2p",
                            confirm_card=lambda tool, args: cards.append((tool, args)))
        finally:
            import unittest.mock as _um
            _um.patch.stopall()
        assert cards == [("emergency_halt", "{}")] and not sent   # 发卡不回文本

    def test_llm_failure_replies_error(self):
        from src.im_bot.handlers import handle_incoming
        sent = []
        for p in self._chat_ctx([RuntimeError("boom")]):
            p.start()
        try:
            from src.im_bot.handlers import handle_incoming
            handle_incoming("feishu", 7, "ou_x", "hi", sent.append, "p2p")
        finally:
            import unittest.mock as _um
            _um.patch.stopall()
        assert any("重发" in s for s in sent)   # 文案师：异常不进用户面，给重发指引
