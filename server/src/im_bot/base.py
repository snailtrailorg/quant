"""IM 统一接入抽象层(arch-19 v2,2026-08-21 批 2)。

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
    """IM 平台接入抽象。每家一个子类+DB 配一行(im_bot_config)。

    批11D 两层模型（通道×方式）：通道注册声明**方式集合** ONBOARDING_METHODS，UI 据此自动生成；
    同平台可多方式并存（飞书=qr+form）。旧单值 ONBOARDING 改 @property 派生（顺序无关 any()）。"""

    provider: str                          # 'feishu' | 'dingtalk' | ...
    MODE: str = "webhook"                  # webhook | websocket | long_poll | hybrid
    # 方式注册表：标识 → {kind: manual|interactive, label_key,
    #                   fields?: [...](manual=FIELD_SCHEMA 本体), wizard?: str(interactive 向导标识),
    #                   post_steps?: [str](创建后指引文案 key——webhook 型通道用)}
    ONBOARDING_METHODS: dict[str, dict] = {}

    @property
    def ONBOARDING(self) -> str:
        """兼容派生（批11D）：任一方式 kind=interactive 即 interactive——顺序无关（盲审 A-P1-3/B-P1-2：
        首键派生是顺序地雷，form 排前会把现网 admin 扫码静默断掉）。"""
        if any(m.get("kind") == "interactive" for m in self.ONBOARDING_METHODS.values()):
            return "interactive"
        return "manual"

    # ── 凭证声明(单一真相源;REQUIRED_FIELDS 由 secret 字段推导)──
    FIELD_SCHEMA: list[dict] = []
    # [{key, type: text|secret|select|number|boolean|textarea, label_key,
    #   secret?: bool, options?: [...], option_label_key?: ...}]
    # 平台固定列(name/enabled/priority/description/default_role/lang)不进此表

    @property
    def required_fields(self) -> set[str]:
        return {f["key"] for f in self.FIELD_SCHEMA if f.get("secret")}

    # ── 连接生命周期(B-G1:arch-19 §1 声明面补齐;默认 no-op——纯 webhook 型无长连接)──
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


# Provider 模块名单（批13 A-P1-1：引导遍历——原只 import feishu，新 Provider 永不注册）。
# wecom 随批13B 上：未实现时 import 失败 log skip 不炸（页签/平台能力自然缺席）。
_PROVIDER_MODULES = ("feishu", "dingtalk", "wecom")
_BOOTSTRAPPED = False   # 显式标志而非"registry 非空"判断——任何模块被单独 import（自注册）
                       # 后 registry 即非空，空检查会让其余平台永不注册（批13 全量测试实锤）


def _bootstrap_registry() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True
    from importlib import import_module
    for mod in _PROVIDER_MODULES:
        try:
            import_module(f".{mod}", __package__)
        except ImportError as e:
            logger.info("Provider 模块 %s 未安装/未实现（跳过注册）: %s", mod, e)


def get_im_provider(provider: str) -> IMBotProvider | None:
    _bootstrap_registry()
    return _REGISTRY.get(provider)


def list_providers() -> list[dict]:
    """平台注册表(前端下拉+向导用)。批11D：增 methods 全集（UI 按注册能力自动生成，零硬编码）。
    批13（盲审 B-P0-1）：manual 方式通用补挂 fields=FIELD_SCHEMA——恢复批11D A-P2-7 契约
    （feishu form 砍除时删了挂钩、新 Provider 没接，自助面表单零凭证框）。"""
    _bootstrap_registry()
    out = []
    for p, inst in sorted(_REGISTRY.items()):
        methods = [{**m, "id": mid} for mid, m in inst.ONBOARDING_METHODS.items()]
        for m in methods:
            if m.get("kind") == "manual" and not m.get("fields"):
                m["fields"] = inst.FIELD_SCHEMA
        out.append({"provider": p, "mode": inst.MODE, "onboarding": inst.ONBOARDING,
                    "methods": methods, "field_schema": inst.FIELD_SCHEMA})
    return out

