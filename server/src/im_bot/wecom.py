"""企微 Provider（批13B）——智能机器人 API 模式长连接。

凭证=BotId/Secret（管理后台「安全与管理→管理工具→智能机器人」手动创建→API 模式+长连接）。
send_text/send_card 本批诚实桩（False）——ws 连接活在 pool runner 子进程，web/告警进程
无连接可推帧；跨进程推送通道+chatid 留存设计归告警批（双盲审 A-P1-2/B-P1-1）。
"""
from __future__ import annotations
import asyncio
import logging

from .base import IMBotProvider, register_provider

logger = logging.getLogger("im_bot.wecom")


def _probe_auth(bot_id: str, secret: str, timeout_s: float = 10) -> tuple[bool, str]:
    """同步探活：一次性 ws 握手+认证帧+等 errcode 后断开（test_connection 用）。

    注意（方案 §4 联调清单）：runner 活连接下再开探测连接是否被服务端踢——真机联调首验。"""
    import json
    import uuid
    import websockets

    async def _once():
        async with websockets.connect("wss://openws.work.weixin.qq.com", ping_interval=None,
                                      open_timeout=8) as ws:
            rid = f"aibot_subscribe_{uuid.uuid4().hex[:24]}"
            await ws.send(json.dumps({"cmd": "aibot_subscribe", "headers": {"req_id": rid},
                                      "body": {"bot_id": bot_id, "secret": secret}}))
            raw = await asyncio.wait_for(ws.recv(), timeout=8)
            frame = json.loads(raw)
            errcode = frame.get("errcode", -1)
            if errcode == 0:
                return True, "认证成功"
            return False, f"errcode={errcode} {frame.get('errmsg', '')}"

    try:
        return asyncio.run(asyncio.wait_for(_once(), timeout=timeout_s))
    except asyncio.TimeoutError:
        return False, "探活超时"
    except Exception as e:
        return False, f"连接失败: {e}"


class WecomProvider(IMBotProvider):
    provider = "wecom"
    MODE = "websocket"
    ONBOARDING_METHODS = {
        "form": {"kind": "manual", "label_key": "imBots.method.form",
                 "post_steps": ["imBots.wecom.step1", "imBots.wecom.step2", "imBots.wecom.step3"]},
    }

    FIELD_SCHEMA = [
        {"key": "bot_id", "type": "text", "label_key": "imBots.field.botId", "secret": False},
        {"key": "secret", "type": "text", "label_key": "imBots.field.botSecret", "secret": True},
    ]

    def send_text(self, bot_id: int, receive_id: str, receive_id_type: str, text: str) -> bool:
        """诚实桩（方案 §2.6）——跨进程推送通道归告警批。"""
        return False

    def send_card(self, bot_id: int, receive_id: str, receive_id_type: str, card: dict) -> bool:
        return False

    def verify_callback(self, bot_id: int, headers: dict, body: str):
        return None   # 长连接型无 webhook 回调

    def test_connection(self, bot_id: int) -> tuple[bool, str]:
        from .credentials import get_bot_credentials
        creds = get_bot_credentials(bot_id)
        if not creds.get("bot_id") or not creds.get("secret"):
            return False, "BotId/Secret 未配置"
        return _probe_auth(creds["bot_id"], creds["secret"])


register_provider(WecomProvider())
