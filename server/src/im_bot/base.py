"""IM 统一接入抽象层(19 号 v2,2026-08-21 批 2)。

IMBotProvider:完整 IM 接入面(区别于 MessageChannel=单向告警出站)。
新平台=实现一个子类(那家的 API/签名/卡片适配)+locales 加字段词条,平台代码零改动。

职责分界:验签/消息解析/卡片 schema=Provider;event_id 去重/60s 时效/角色门槛/challenge
路由=平台(feishu_bot/router 与未来通用入口层)。
"""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("im_bot")

_REGISTRY: dict[str, "IMBotProvider"] = {}


class IMBotProvider(ABC):
    """IM 平台接入抽象。每家一个子类+DB 配一行(im_bot_config)。"""

    provider: str                          # 'feishu' | 'dingtalk' | ...
    MODE: str = "webhook"                  # webhook | websocket | long_poll | hybrid
    ONBOARDING: str = "manual"             # manual | interactive(扫码/回跳类辅助流程)

    # ── 凭证声明(单一真相源;REQUIRED_FIELDS 由 secret 字段推导)──
    FIELD_SCHEMA: list[dict] = []
    # [{key, type: text|secret|select|number|boolean|textarea, label_key,
    #   secret?: bool, options?: [...], option_label_key?: ...}]
    # 平台固定列(name/enabled/priority/description/default_role/lang)不进此表

    @property
    def required_fields(self) -> set[str]:
        return {f["key"] for f in self.FIELD_SCHEMA if f.get("secret")}

    # ── 连接生命周期(B-G1:19 号 §1 声明面补齐;默认 no-op——纯 webhook 型无长连接)──
    def connect(self, bot_id: int, on_message) -> None:
        """启动长连接(websocket/long_poll 型;webhook 型无需)。"""
    def shutdown(self, bot_id: int) -> None:
        """停长连接。"""

    # ── 通道行为 ──
    @abstractmethod
    def send_text(self, bot_id: int, receive_id: str, receive_id_type: str, text: str) -> bool:
        ...

    @abstractmethod
    def send_card(self, bot_id: int, receive_id: str, receive_id_type: str, card: dict) -> bool:
        ...

    def verify_callback(self, bot_id: int, headers: dict, body: str):
        """验签+解析回调。返回:
        ("challenge", {...回显}) | ("message", dict) | ("card", dict) | None(验签失败)
        批 2 飞书仍走 feishu_bot/router 旧路径(用户飞书后台已配的 URL 不动);
        /api/im-bots/{bid}/callback 通用入口就绪后此方法成为主路径(批 3 强制)。
        """
        return None

    def build_confirm(self, tool: str, args: dict, reason: str = "") -> dict:
        """操作确认卡片(各家 schema)。默认极简文本卡——子类按需覆写。"""
        return {"type": "text", "text": f"确认执行 {tool} {args}? (1=是/0=否, {reason})"}

    def parse_confirm(self, action_value: dict) -> dict | None:
        """卡片按钮值 → {tool, args, ts}。默认透传。"""
        return action_value or None

    @abstractmethod
    def test_connection(self, bot_id: int) -> tuple[bool, str]:
        """连通测试。返回 (ok, detail)。"""
        ...

    # ── 接入向导(ONBOARDING=interactive 才有意义)──
    def start_onboarding(self) -> dict:
        """启动辅助接入(扫码/回跳)。返回 {"type": "qr"|"url", ...}。默认不支持。"""
        raise NotImplementedError(f"{self.provider} 不支持辅助接入(manual)")

    def poll_onboarding(self, ticket: str) -> dict:
        """轮询接入状态:{"status": pending|done|failed, credentials?: {...}, qr?: ...}"""
        raise NotImplementedError(f"{self.provider} 不支持辅助接入(manual)")


def register_provider(inst: IMBotProvider) -> None:
    _REGISTRY[inst.provider] = inst


def get_im_provider(provider: str) -> IMBotProvider | None:
    if not _REGISTRY:
        from . import feishu as _feishu  # noqa: F401 触发注册
    return _REGISTRY.get(provider)


def list_providers() -> list[dict]:
    """平台注册表(前端下拉+向导用)。"""
    if not _REGISTRY:
        from . import feishu as _feishu  # noqa: F401
    return [{"provider": p, "mode": inst.MODE, "onboarding": inst.ONBOARDING,
             "field_schema": inst.FIELD_SCHEMA}
            for p, inst in sorted(_REGISTRY.items())]
