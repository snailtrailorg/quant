"""批27-7：群聊限只读（用户裁定）——perms 钳 {"read"}（根修：LLM 看不到操作工具）+
tool_calls 层兜底拒答；操作确认仅私聊。"""
from unittest.mock import MagicMock, patch


def _identity(perms=None):
    return {"user_id": 9, "username": "u9", "role": "trader", "perms": perms or {"read", "trade", "halt"}}


def _run(chat_type, tool_calls=None):
    from src.im_bot.handlers import handle_incoming
    replies = []
    gw = MagicMock()
    resp = MagicMock()
    resp.tool_calls = tool_calls or []
    resp.content = "查询结果"
    gw.chat.return_value = resp
    with patch("src.im_bot.users.resolve_im_identity", return_value=_identity()), \
         patch("src.llm_gateway.gateway", gw), \
         patch("src.llm_gateway.gateway.READ_TOOLS", []), \
         patch("src.llm_gateway.gateway.OPERATIONAL_TOOLS", [MagicMock(name="emergency_halt")]):
        # OPERATIONAL_TOOLS 元素 .name —— MagicMock(name=) 不设 .name 属性，用真实小对象
        from types import SimpleNamespace
        import src.im_bot.handlers as H
        import src.llm_gateway.gateway as G
        G.OPERATIONAL_TOOLS = [SimpleNamespace(name="emergency_halt")]
        G.READ_TOOLS = []
        card_calls = []
        handle_incoming("feishu", 1, "ou_x", "停掉策略", lambda t: replies.append(t),
                        chat_type, confirm_card=lambda *a: card_calls.append(a))
    return replies, card_calls, gw


def test_group_perms_clamped_to_read():
    """根修：群聊时 gateway 收到 perms={'read'}——LLM 看不到操作工具。"""
    replies, _, gw = _run("group")
    assert gw.chat.call_args.kwargs.get("perms") == {"read"}


def test_p2p_perms_untouched():
    """私聊 perms 原样透传（五轮裁定不变）。"""
    _, _, gw = _run("p2p")
    assert gw.chat.call_args.kwargs.get("perms") == {"read", "trade", "halt"}


def test_group_operational_toolcall_rejected_no_card():
    """兜底：群聊下 LLM 幻觉调操作工具——拒答文本、不发确认卡。"""
    tc = {"id": "1", "name": "emergency_halt", "arguments": "{}"}
    replies, card_calls, _ = _run("group", tool_calls=[tc])
    assert any("私聊" in r or "网页" in r for r in replies), "应拒答并指路"
    assert not card_calls, "群聊不得发确认卡"


def test_p2p_operational_toolcall_sends_card():
    """私聊：操作工具 → 发确认卡（原行为不变）。"""
    tc = {"id": "1", "name": "emergency_halt", "arguments": "{}"}
    _, card_calls, _ = _run("p2p", tool_calls=[tc])
    assert len(card_calls) == 1 and card_calls[0][0] == "emergency_halt"
