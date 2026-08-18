"""飞书集成测试（#35 第 6 层）：飞书消息链路。

mock 飞书 API / DB，不依赖真实 lark 凭证。
覆盖：FeishuClient 初始化 / token 获取 / 消息发送 / webhook 消息处理 / 签名校验。
"""
import os
import json
import hashlib
from unittest.mock import patch, MagicMock, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# FeishuClient 初始化
# ---------------------------------------------------------------------------

def test_feishu_client_init_from_db():
    """FeishuClient.__init__ 从 DB 读 feishu_config，app_id/app_secret 正确解密。"""
    from src.feishu_bot.bot import FeishuClient

    mock_row = ("cli_aafcd6f818b8dbd1", "encrypted_secret_here")
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = mock_row
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.execute.return_value = mock_cur

    with patch("src.data_platform.db.get_conn", return_value=mock_conn), \
         patch("src.quant_common.crypto.decrypt", return_value="decrypted_app_secret"):
        client = FeishuClient()

    assert client.app_id == "cli_aafcd6f818b8dbd1"
    assert client.app_secret == "decrypted_app_secret"
    assert client._token == ""
    assert client._token_expires == 0


def test_feishu_client_init_no_db():
    """DB 无 feishu_config，app_id/app_secret 为空，不抛异常。"""
    from src.feishu_bot.bot import FeishuClient

    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = None
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.execute.return_value = mock_cur

    with patch("src.data_platform.db.get_conn", return_value=mock_conn):
        client = FeishuClient()

    assert client.app_id == ""
    assert client.app_secret == ""


def test_feishu_client_init_db_error():
    """DB 查询抛异常，app_id 为空，不崩溃。"""
    from src.feishu_bot.bot import FeishuClient

    with patch("src.data_platform.db.get_conn", side_effect=Exception("DB error")):
        client = FeishuClient()

    assert client.app_id == ""
    assert client.app_secret == ""


# ---------------------------------------------------------------------------
# _get_token
# ---------------------------------------------------------------------------

def test_feishu_client_get_token():
    """_get_token mock httpx.post，验证 token 获取和缓存。"""
    from src.feishu_bot.bot import FeishuClient

    # mock httpx 返回正常 token
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"tenant_access_token": "tok_abc123", "expire": 7200}

    with patch("src.data_platform.db.get_conn") as mock_get_conn, \
         patch("httpx.post", return_value=mock_resp) as mock_post:
        client = FeishuClient()
        # 覆盖 __init__ 设置（mock get_conn 无返回时 app_id 为空，手动设）
        client.app_id = "test_app"
        client.app_secret = "test_secret"
        token = client._get_token()

    assert token == "tok_abc123"
    assert client._token == "tok_abc123"
    mock_post.assert_called_once_with(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": "test_app", "app_secret": "test_secret"},
        timeout=10,
    )


def test_feishu_client_get_token_no_credentials():
    """无 app_id/app_secret 返回空 token。"""
    from src.feishu_bot.bot import FeishuClient

    with patch("src.data_platform.db.get_conn"):
        client = FeishuClient()
        client.app_id = ""
        client.app_secret = ""

    token = client._get_token()
    assert token == ""


def test_feishu_client_get_token_cached():
    """token 未过期时不重新请求。"""
    from src.feishu_bot.bot import FeishuClient
    import time

    with patch("src.data_platform.db.get_conn"):
        client = FeishuClient()
        client.app_id = "test_app"
        client.app_secret = "test_secret"
        client._token = "cached_token"
        client._token_expires = time.time() + 3600  # 1h 后过期

    with patch("httpx.post") as mock_post:
        token = client._get_token()

    assert token == "cached_token"
    mock_post.assert_not_called()  # 未重新请求


def test_feishu_client_get_token_expired():
    """token 过期后重新请求。"""
    from src.feishu_bot.bot import FeishuClient
    import time

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"tenant_access_token": "new_token", "expire": 7200}

    with patch("src.data_platform.db.get_conn"), \
         patch("httpx.post", return_value=mock_resp) as mock_post:
        client = FeishuClient()
        client.app_id = "test_app"
        client.app_secret = "test_secret"
        client._token = "old_token"
        client._token_expires = time.time() - 60  # 已过期
        token = client._get_token()

    assert token == "new_token"
    mock_post.assert_called_once()


def test_feishu_client_get_token_api_failure():
    """飞书 API 返回异常，返回空 token。"""
    from src.feishu_bot.bot import FeishuClient

    with patch("src.data_platform.db.get_conn"):
        client = FeishuClient()
        client.app_id = "test_app"
        client.app_secret = "test_secret"

    with patch("httpx.post", side_effect=Exception("API timeout")):
        token = client._get_token()

    assert token == ""


# ---------------------------------------------------------------------------
# send_text / send_card
# ---------------------------------------------------------------------------

def test_feishu_client_send_text():
    """send_text mock httpx.post，验证正确调飞书 API。"""
    from src.feishu_bot.bot import FeishuClient

    with patch("src.data_platform.db.get_conn"):
        client = FeishuClient()
        client._token = "tok_valid"
        client._token_expires = 9999999999.0

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        client.send_text("ou_xxx", "你好 飞书", "open_id")

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["params"]["receive_id_type"] == "open_id"
    assert call_kwargs["headers"]["Authorization"] == "Bearer tok_valid"
    assert call_kwargs["json"]["receive_id"] == "ou_xxx"
    assert call_kwargs["json"]["msg_type"] == "text"

    # 验证 content 是序列化后的 JSON（含 text）
    content = json.loads(call_kwargs["json"]["content"])
    assert content["text"] == "你好 飞书"


def test_feishu_client_send_text_no_token():
    """无 token 时不发送消息。"""
    from src.feishu_bot.bot import FeishuClient

    with patch("src.data_platform.db.get_conn"):
        client = FeishuClient()
        client._token = ""

    with patch("httpx.post") as mock_post:
        client.send_text("ou_xxx", "test")

    mock_post.assert_not_called()


def test_feishu_client_send_card():
    """send_card 发送交互卡片，msg_type=interactive。"""
    from src.feishu_bot.bot import FeishuClient

    with patch("src.data_platform.db.get_conn"):
        client = FeishuClient()
        client._token = "tok_valid"
        client._token_expires = 9999999999.0

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    card = {"config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": "测试卡片"}}}

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        client.send_card("ou_xxx", card, "open_id")

    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["msg_type"] == "interactive"
    # content 是 card dict 的 JSON 序列化
    content = json.loads(call_kwargs["json"]["content"])
    assert content["header"]["title"]["content"] == "测试卡片"


# ---------------------------------------------------------------------------
# 签名校验
# ---------------------------------------------------------------------------

def test_verify_signature():
    """verify_signature 正确校验飞书 Webhook 签名。"""
    from src.feishu_bot.bot import verify_signature

    secret = "test_secret_123"
    timestamp = "1723536000"
    body = '{"challenge": "test_challenge"}'

    expected_sig = hashlib.sha256(f"{timestamp}{secret}{body}".encode()).hexdigest()

    with patch.dict(os.environ, {"LARK_VERIFICATION_TOKEN": secret}):
        assert verify_signature(timestamp, body, expected_sig) is True
        assert verify_signature(timestamp, body, "wrong_sig") is False


def test_verify_signature_no_secret():
    """未配置 LARK_VERIFICATION_TOKEN 时跳过校验（返回 True）。"""
    from src.feishu_bot.bot import verify_signature

    with patch.dict(os.environ, {}, clear=True):
        assert verify_signature("ts", "body", "sig") is True


# ---------------------------------------------------------------------------
# Webhook 消息处理流程
# ---------------------------------------------------------------------------

def test_feishu_webhook_message_flow():
    """模拟 Webhook 收到消息 → process_message_async 被调用 → LLM 网关处理。

    验证：
    1. process_message_async 调用 gateway.chat
    2. 读类工具直接执行并返回结果
    3. 最终回复发送给用户
    """
    from src.feishu_bot.bot import process_message_async, FeishuClient
    from src.llm_gateway import gateway as llm_gateway
    from src.llm_gateway.gateway import LLMResponse

    with patch.object(llm_gateway, "chat", return_value=LLMResponse(content="您好，我是量化交易助手。当前风控状态正常。")) as mock_chat, \
         patch("src.feishu_bot.bot.FeishuClient.send_text") as mock_send, \
         patch("src.feishu_bot.bot.check_user", return_value="analyst"):
        process_message_async("ou_test_user", "查一下风控状态", "open_id")

    # 验证 LLM 被调用
    mock_chat.assert_called_once()
    assert mock_chat.call_args.kwargs.get("role") == "analyst"
    assert mock_chat.call_args[0][0][-1]["content"] == "查一下风控状态"

    # 验证回复发送给用户
    mock_send.assert_called_once()
    assert "您好" in mock_send.call_args[0][1]


def test_feishu_webhook_message_unauthorized():
    """未授权用户收到拒绝消息。"""
    from src.feishu_bot.bot import process_message_async

    with patch("src.feishu_bot.bot.FeishuClient.send_text") as mock_send, \
         patch("src.feishu_bot.bot.check_user", return_value=None):
        process_message_async("ou_unauthorized", "查持仓", "open_id")

    mock_send.assert_called_once()
    assert "未授权" in mock_send.call_args[0][1]


def test_feishu_process_message_with_tool():
    """消息触发读类工具调用，工具结果回传 LLM 后回复。"""
    from src.feishu_bot.bot import process_message_async, FeishuClient
    from src.llm_gateway import gateway as llm_gateway
    from src.llm_gateway.gateway import LLMResponse

    with patch.object(llm_gateway, "chat") as mock_chat, \
         patch("src.feishu_bot.bot.FeishuClient.send_text") as mock_send, \
         patch("src.feishu_bot.bot.check_user", return_value="trader"):
        # 第一轮：LLM 返回工具调用（query_risk_state）
        # 第二轮：LLM 返回最终回复
        mock_chat.side_effect = [
            LLMResponse(
                content="",
                tool_calls=[{
                    "id": "call_1",
                    "name": "query_risk_state",
                    "arguments": "{}",
                }],
            ),
            LLMResponse(content="风控状态正常，您可以继续交易。"),
        ]
        process_message_async("ou_trader", "当前风控状态", "open_id")

    # LLM 被调用了 2 次（第1轮工具调用，第2轮最终回复）
    assert mock_chat.call_count == 2

    # 工具结果被追加到 messages
    second_call_args = mock_chat.call_args[0][0]
    assert any("风控状态" in str(m) for m in second_call_args)

    # 最终回复发送给用户
    mock_send.assert_called_once()
    assert "风控状态正常" in mock_send.call_args[0][1]