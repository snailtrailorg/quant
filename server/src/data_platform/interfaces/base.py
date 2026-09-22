"""外部接口插件化接入层（批 63）——对标 im_bot/base.py 的 FIELD_SCHEMA 机制。

InterfaceProvider：每家接口供应商一个子类，声明「凭证字段 schema + 参数字段 schema」，
前端据 schema 动态生成配置表单（弹窗增删改），凭证整包加密存 external_interface.credentials_encrypted、
参数明文存 params JSONB。

与 im_bot 的两点差异：
1. 无「接入向导/长连接/卡片」等 IM 特有概念——外部接口只需「配置项自描述 + 连接测试」。
2. 多一张 PARAMS_SCHEMA——外部接口有「地址/端口/客户号」等非秘密连接参数（IM 只有凭证）。

能力真源仍=代码（adapters/base.py adapter.capabilities + markets.NON_DATA_PROVIDERS 网关直标），
本层只声明「配置面」元数据（schema），不重复声明能力。
"""
from __future__ import annotations
import logging
from abc import ABC

logger = logging.getLogger("data_platform.interfaces")

_REGISTRY: dict[str, "InterfaceProvider"] = {}


class InterfaceProvider(ABC):
    """外部接口供应商接入抽象。每家一个子类 + external_interface 配一行（行=账号）。

    FIELD_SCHEMA：凭证字段（身份秘密：账号/密码/密钥），secret 字段前端 password 框 + 写侧必填，
                  整包 JSON Fernet 加密存 credentials_encrypted。
    PARAMS_SCHEMA：参数字段（连接配置：地址/端口/客户号，非秘密），明文存 params JSONB。
    schema 元素：{key, type: text|select|number|boolean|textarea, label_key,
                 secret?: bool, options?: [...], option_label_key?: ...}
    平台固定列（name/provider/market/exchanges/capabilities/enabled/position）不进 schema。
    """

    provider: str                      # 'xtp' | 'emt_emq' | 'tushare' | ...
    market: str                        # 'astock' | 'crypto'
    default_exchanges: list[str] = []  # perp 预填单所（对齐 mgmt._default_exchanges 语义）
    FIELD_SCHEMA: list[dict] = []
    PARAMS_SCHEMA: list[dict] = []

    @property
    def required_fields(self) -> set[str]:
        """secret 字段集（写侧校验非空；同 im_bot base.py:56-58）。"""
        return {f["key"] for f in self.FIELD_SCHEMA if f.get("secret")}


def register_provider(inst: InterfaceProvider) -> None:
    _REGISTRY[inst.provider] = inst


# Provider 模块名单（bootstrap 遍历，对标 im_bot _PROVIDER_MODULES——原只 import 首个，
# 新 Provider 永不注册）。jqq/ricequant 无 schema（stub 无凭证字段）不入此表。
_PROVIDER_MODULES = ("tushare", "tencent", "xtp", "binance_perp", "okx_perp", "emt_emq")
_BOOTSTRAPPED = False


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
            logger.info("接口 provider 模块 %s 未实现（跳过）: %s", mod, e)


def get_interface_provider(provider: str) -> InterfaceProvider | None:
    _bootstrap_registry()
    return _REGISTRY.get(provider)


def list_interface_schemas() -> dict[str, dict]:
    """provider → {field_schema, params_schema}（mgmt.list_interface_providers 补吐用）。"""
    _bootstrap_registry()
    return {p: {"field_schema": inst.FIELD_SCHEMA, "params_schema": inst.PARAMS_SCHEMA}
            for p, inst in _REGISTRY.items()}
