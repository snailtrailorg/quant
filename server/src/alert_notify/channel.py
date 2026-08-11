"""消息通道抽象基类 + 实现（平台化：别人实现接口接入自己的渠道）。

接口：send(title, body, level) / test()。
实现：WechatWorkChannel / DiscordChannel / ServerChanChannel（包装现有 alert_notify 渠道逻辑）。
别人加钉钉/邮件：实现 MessageChannel 子类 + DB 配置（provider='dingtalk'），不改 alert_notify。
"""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("message_channel")


class MessageChannel(ABC):
    """消息通道接口（AI 输出层 / 告警通道统一抽象）。"""

    @abstractmethod
    def send(self, title: str, body: str, level: str = "info") -> bool:
        """发送消息。返回是否成功。"""

    @abstractmethod
    def test(self) -> bool:
        """测试连接（发一条测试消息）。"""


class _WebhookChannel(MessageChannel):
    """通用 webhook 通道基类（企业微信/Discord 都是 webhook）。"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def _post(self, payload: dict) -> bool:
        import httpx
        try:
            r = httpx.post(self.webhook_url, json=payload, timeout=10)
            return r.status_code == 200
        except Exception as e:
            logger.warning(f"{self.__class__.__name__} 发送失败: {e}")
            return False


class WechatWorkChannel(_WebhookChannel):
    """企业微信群机器人（webhook URL）。"""
    def send(self, title, body, level="info"):
        return self._post({"msgtype": "markdown", "markdown": {"content": f"**[{level}] {title}**\n{body}"}})
    def test(self):
        return self.send("通道测试", "quant 平台消息通道测试", "info")


class DiscordChannel(_WebhookChannel):
    """Discord webhook。"""
    def send(self, title, body, level="info"):
        return self._post({"content": f"**[{level}] {title}**\n{body}"})
    def test(self):
        return self.send("通道测试", "quant channel test", "info")


class ServerChanChannel(MessageChannel):
    """Server 酱（sckey）。"""
    def __init__(self, sckey: str):
        self.sckey = sckey
    def send(self, title, body, level="info"):
        import httpx
        try:
            r = httpx.post(f"https://sctapi.ftqq.com/{self.sckey}.send",
                           data={"title": f"[{level}] {title}", "desp": body}, timeout=10)
            return r.status_code == 200
        except Exception as e:
            logger.warning(f"ServerChan 发送失败: {e}")
            return False
    def test(self):
        return self.send("通道测试", "quant channel test", "info")


# ── 注册表：provider -> MessageChannel 类 ──
_REGISTRY: dict[str, type[MessageChannel]] = {
    "wechat_work": WechatWorkChannel,
    "discord": DiscordChannel,
    "serverchan": ServerChanChannel,
}


def get_channel(provider: str) -> MessageChannel | None:
    """从 DB channel_config 实例化渠道（credentials 解密）。"""
    cls = _REGISTRY.get(provider)
    if not cls:
        return None
    try:
        from src.data_platform.db import get_conn
        from src.web_api.crypto_utils import decrypt
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT credentials_encrypted FROM channel_config "
                "WHERE provider=%s AND enabled=true LIMIT 1", (provider,))
            r = cur.fetchone()
        if not r:
            return None
        cred = decrypt(r[0]) if r[0] else ""
        return cls(cred)
    except Exception as e:
        logger.warning(f"读 channel_config({provider}) 失败: {e}")
        return None
