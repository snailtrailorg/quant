"""D32 统一注册表（batch 172a）——三域同一张表，唯一声明处。

域词表与 `quant_common/markets.py` 同词法：
- `data_source`   数据源（由 A03 的 `@adapter` 注册，P7 落地）
- `trading_account` 交易账号（MD/TD 插件）
- `research`      非结构化研究

键是 `(domain, provider)` 复合：`binance`（数据源）与 `binance_perp`（交易通道）
是两把键、同一张表。

本模块只做注册/取用/能力协商，不读 DB、不 import 第三方。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

DOMAIN_DATA = "data_source"
DOMAIN_TRADING = "trading_account"
DOMAIN_RESEARCH = "research"

DOMAINS: frozenset[str] = frozenset({DOMAIN_DATA, DOMAIN_TRADING, DOMAIN_RESEARCH})

# 端口域词表（与数据域 CAPABILITIES 分离：后者是 DataKind 的派生归类视图）
PORT_CAPS: frozenset[str] = frozenset({
    "execution", "market_data", "research", "history", "account",
})

_REGISTRY: dict[tuple[str, str], Any] = {}
_LEGACY: set[tuple[str, str]] = set()

# 公开只读视图（映射是动态的：注册后立即可见；调用方不得写入）
REGISTRY: Mapping[tuple[str, str], Any] = MappingProxyType(_REGISTRY)


class UnsupportedCapability(Exception):
    """请求的端口能力不在插件的 `manifest.port_caps` 内。"""


class UnknownProvider(KeyError):
    """`(domain, provider)` 未注册。"""


def _manifest_field(plugin: Any, name: str) -> Any:
    manifest = getattr(plugin, "manifest", None)
    if manifest is None:
        raise TypeError(f"{plugin!r} 缺少 manifest")
    if isinstance(manifest, dict):
        return manifest.get(name)
    return getattr(manifest, name, None)


def _port_caps(plugin: Any) -> set[str]:
    """兼容 manifest 为 dataclass 或 dict 两种写法。"""
    caps = _manifest_field(plugin, "port_caps")
    if caps is None:
        raise TypeError("manifest 缺少 port_caps")
    return set(caps)


def register(domain: str, provider: str, plugin: Any, *, legacy_only: bool = False) -> None:
    """注册插件。重复注册同一键视为错误（防静默覆盖）。

    import 时即校验：provider 为非空 str、manifest 存在、port_caps ⊆ PORT_CAPS、
    `build` 可调用；manifest 若声明了 id/domain，须与注册实参一致。
    生产误注册在启动暴露，不等 pick 或调用点才炸。

    `legacy_only=True`：把旧注册点登记为投影（P3/P4 迁移期用，见 D32 §十）。
    """
    if domain not in DOMAINS:
        raise ValueError(f"未知域 {domain!r}；合法值：{sorted(DOMAINS)}")
    if not isinstance(provider, str) or not provider or provider != provider.strip():
        raise ValueError(f"provider 必须是非空、无首尾空白的 str，收到 {provider!r}")
    caps = _port_caps(plugin)                       # 缺 manifest / port_caps 抛 TypeError
    unknown = caps - PORT_CAPS
    if unknown:
        raise ValueError(f"({domain}, {provider}) 声明了未定义的端口能力：{sorted(unknown)}")
    if not callable(getattr(plugin, "build", None)):
        raise ValueError(f"({domain}, {provider}) 的 plugin 缺少可调用的 build()")
    declared_id = _manifest_field(plugin, "id")
    if declared_id is not None and declared_id != provider:
        raise ValueError(f"manifest.id={declared_id!r} 与注册 provider={provider!r} 不一致")
    declared_domain = _manifest_field(plugin, "domain")
    if declared_domain is not None and declared_domain != domain:
        raise ValueError(f"manifest.domain={declared_domain!r} 与注册 domain={domain!r} 不一致")

    key = (domain, provider)
    if key in _REGISTRY and _REGISTRY[key] is not plugin:
        raise ValueError(f"重复注册：{key}")
    _REGISTRY[key] = plugin
    if legacy_only:
        _LEGACY.add(key)
    else:
        _LEGACY.discard(key)


def get(domain: str, provider: str) -> Any:
    try:
        return _REGISTRY[(domain, provider)]
    except KeyError:
        raise UnknownProvider(f"未注册：({domain}, {provider})") from None


def pick(domain: str, provider: str, need: set[str], impl: str) -> Any:
    """按能力协商选插件。

    `impl` 只用于审计与错误定位（legacy/plugin 双轨由账号行 `impl` 列决定，
    不参与本层选择）。
    """
    plugin = get(domain, provider)
    missing = set(need) - _port_caps(plugin)
    if missing:
        raise UnsupportedCapability(
            f"({domain}, {provider}, impl={impl}) 缺少端口能力：{sorted(missing)}"
        )
    return plugin


def registered() -> list[tuple[str, str]]:
    return sorted(_REGISTRY)


def legacy_registered() -> list[tuple[str, str]]:
    """迁移期的 legacy 投影键（P3/P4 逐项删除；P5 后应为空）。"""
    return sorted(_LEGACY)


def clear() -> None:
    """仅供测试清理。生产代码不得调用。"""
    _REGISTRY.clear()
    _LEGACY.clear()
