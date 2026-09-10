"""钉钉 Provider（批13）——Stream 长连接模式。

凭证=AppKey/AppSecret（开放平台开发者后台建企业内部应用→机器人→Stream 模式）。
协议事实（官方 dingtalk-stream SDK 源码级核实，2026-09-10）：
- 建连：POST api.dingtalk.com/v1.0/gateway/connections/open → endpoint+ticket → wss
- robotCode = client_id（SDK chatbot.py 三处同判）
- accessToken：POST /v1.0/oauth2/accessToken
- 被动回复=sessionWebhook（免 token）；主动推送=oToMessages/batchSend（本批实现供告警链后续消费）
"""
from __future__ import annotations
import json
import logging

import httpx

from .base import IMBotProvider, register_provider

logger = logging.getLogger("im_bot.dingtalk")

_API = "https://api.dingtalk.com"


def fetch_access_token(app_key: str, app_secret: str) -> tuple[str, str]:
    """拉 accessToken。返回 (token, err)——失败时 token 空、err 带原因（凭证预检/连通测试共用）。"""
    if not app_key or not app_secret:
        return "", "AppKey/AppSecret 未配置"
    try:
        resp = httpx.post(f"{_API}/v1.0/oauth2/accessToken",
                          json={"appKey": app_key, "appSecret": app_secret}, timeout=10)
        if resp.status_code != 200:
            return "", f"HTTP {resp.status_code}: {resp.text[:120]}"
        data = resp.json()
        token = data.get("accessToken", "")
        if not token:
            return "", f"API 拒绝: {resp.text[:120]}"
        return token, ""
    except Exception as e:
        return "", f"请求失败: {e}"


class DingtalkProvider(IMBotProvider):
    provider = "dingtalk"
    MODE = "websocket"
    ONBOARDING_METHODS = {
        "form": {"kind": "manual", "label_key": "imBots.method.form",
                 "post_steps": ["imBots.dingtalk.step1", "imBots.dingtalk.step2", "imBots.dingtalk.step3"]},
    }

    FIELD_SCHEMA = [
        {"key": "app_key", "type": "text", "label_key": "imBots.field.appKey", "secret": False},
        {"key": "app_secret", "type": "text", "label_key": "imBots.field.appSecret", "secret": True},
    ]

    def send_text(self, bot_id: int, receive_id: str, receive_id_type: str, text: str) -> bool:
        """主动单聊推送（oToMessages/batchSend；robotCode=app_key——官方 SDK 语义）。
        receive_id=staffId。本批供告警链后续消费。"""
        from .credentials import get_bot_credentials
        creds = get_bot_credentials(bot_id)
        app_key, app_secret = creds.get("app_key", ""), creds.get("app_secret", "")
        token, err = fetch_access_token(app_key, app_secret)
        if not token:
            logger.warning("钉钉 send_text 取 token 失败 bot=%s: %s", bot_id, err)
            return False
        try:
            resp = httpx.post(
                f"{_API}/v1.0/robot/oToMessages/batchSend",
                headers={"x-acs-dingtalk-access-token": token},
                json={"robotCode": app_key, "userIds": [receive_id],
                      "msgKey": "sampleText", "msgParam": json.dumps({"content": text})},
                timeout=10)
            if resp.status_code != 200:
                logger.warning("钉钉 send_text HTTP %s: %s", resp.status_code, resp.text[:120])
                return False
            return True
        except Exception as e:
            logger.warning("钉钉 send_text 失败: %s", e)
            return False

    def send_card(self, bot_id: int, receive_id: str, receive_id_type: str, card: dict) -> bool:
        """卡片 schema 二期（方案 §2.6：本批诚实桩）。"""
        return False

    def verify_callback(self, bot_id: int, headers: dict, body: str):
        """长连接型无 webhook 回调（基类默认，显式声明语义）。"""
        return None

    def test_connection(self, bot_id: int) -> tuple[bool, str]:
        from .credentials import get_bot_credentials
        creds = get_bot_credentials(bot_id)
        token, err = fetch_access_token(creds.get("app_key", ""), creds.get("app_secret", ""))
        return (True, "accessToken 获取成功") if token else (False, err or "凭证未配置")


register_provider(DingtalkProvider())
