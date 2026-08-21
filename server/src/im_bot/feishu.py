"""飞书 Provider(19 号 v2 批 2)——适配层组合现有生产验证逻辑,行为零变化。

MODE='hybrid':消息走 ws 长连接(feishu_bot/ws_client),卡片回调走 webhook
(feishu_bot/router,lark SDK 长连接丢卡片是现状)。
"""
from __future__ import annotations
import logging

from .base import IMBotProvider, register_provider

logger = logging.getLogger("im_bot.feishu")


class FeishuProvider(IMBotProvider):
    provider = "feishu"
    MODE = "hybrid"
    ONBOARDING = "interactive"      # lark.register_app 扫码

    FIELD_SCHEMA = [
        {"key": "app_id", "type": "text", "label_key": "imBots.field.appId", "secret": False},
        {"key": "app_secret", "type": "text", "label_key": "imBots.field.appSecret", "secret": True},
        {"key": "verification_token", "type": "text", "label_key": "imBots.field.verifyToken", "secret": True},
        {"key": "encrypt_key", "type": "text", "label_key": "imBots.field.encryptKey", "secret": True},
    ]

    # ── 通道行为(委托 feishu_bot.bot 的生产实现,bot_id 定位凭证)──
    def send_text(self, bot_id: int, receive_id: str, receive_id_type: str, text: str) -> bool:
        from .feishu_client import FeishuClient
        client = FeishuClient(bot_id)
        client.send_text(receive_id, text, receive_id_type)
        return True

    def send_card(self, bot_id: int, receive_id: str, receive_id_type: str, card: dict) -> bool:
        from .feishu_client import FeishuClient
        client = FeishuClient(bot_id)
        client.send_card(receive_id, card, receive_id_type)
        return True

    def verify_callback(self, bot_id: int, headers: dict, body: str):
        """批 2:飞书回调仍走 feishu_bot/router 旧路径(用户飞书后台已配 URL 不动,
        通用 /api/im-bots/{bid}/callback 批 3 强制)——此实现为通用入口预置。"""
        from .feishu_client import verify_card_signature, verify_event_signature
        ts = headers.get("X-Lark-Timestamp", "")
        nonce = headers.get("X-Lark-Nonce", "")
        sig = headers.get("X-Lark-Signature", "")
        if not verify_card_signature(ts, nonce, body, sig):
            return None
        import json as _json
        data = _json.loads(body)
        if "challenge" in data:
            return ("challenge", {"challenge": data["challenge"]})
        if data.get("event", {}).get("action"):
            return ("card", data)
        return ("message", data)

    def test_connection(self, bot_id: int) -> tuple[bool, str]:
        """tenant_access_token 获取即连通(同旧 /api/feishu/{fid}/test 逻辑,凭证读新表)。"""
        import requests
        from .credentials import get_bot_credentials
        creds = get_bot_credentials(bot_id)
        if not creds.get("app_id") or not creds.get("app_secret"):
            return False, "app_id/app_secret 未配置(扫码接入或手动补录)"
        try:
            resp = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": creds["app_id"], "app_secret": creds["app_secret"]}, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                tok = data.get("tenant_access_token", "")
                return True, f"token={tok[:10]}..."
            return False, data.get("msg", str(data))
        except Exception as e:
            return False, str(e)


register_provider(FeishuProvider())
