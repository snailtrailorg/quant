"""行情实体载体桥（过渡）：把 legacy md_gateway 注册表投影到统一 registry 的行情域。

为什么需要：2026-11-22 裁定「域=接口类型」，provider 在每个域各是一个实体（XTP = 行情
+ 交易两条，无数据实体）。行情侧原生插件（D32 P4/P5）尚未逐家落地，但 md_gateway 的
@register_md_gateway 已声明了各 provider 的网关——本桥把它投影成
`(market_interface, provider)` 实体（port_caps={market_data}），使 pick()/目录/部署
条件对行情侧同样可用。

不是第二真源：载体仍由 md_gateway 注册表声明；provider 原生行情插件落地后逐个替换，
全部替换完即删本文件（届时 md_gateway 注册表退役）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.registry import DOMAIN_MARKET, UnsupportedCapability, register


@dataclass(frozen=True)
class Manifest:
    id: str
    domain: str = DOMAIN_MARKET
    port_caps: frozenset[str] = frozenset({"market_data"})


class MdGatewayPort:
    """legacy MdGateway → MarketDataPort 形状（subscribe/unsubscribe 收 list）。"""

    def __init__(self, gw: Any) -> None:
        self._gw = gw

    def subscribe(self, symbols) -> None:
        for s in (symbols if isinstance(symbols, (list, tuple, set)) else [symbols]):
            self._gw.subscribe(s)

    def unsubscribe(self, symbols) -> None:
        for s in (symbols if isinstance(symbols, (list, tuple, set)) else [symbols]):
            self._gw.unsubscribe(s)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._gw, name)


class LegacyMarketPlugin:
    """单个 provider 的行情实体（包 legacy 网关）。"""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.manifest = Manifest(id=provider)

    def build(self, counters: Any, role: str, slot: Any = None) -> Any:
        if role != "md":
            raise UnsupportedCapability(f"行情实体不支持 role={role!r}")
        from src.strategy_framework.md_gateway import create_md_gateway
        return MdGatewayPort(create_md_gateway(self.provider, counters))


def register_market_bridge() -> None:
    """按 md_gateway 注册表补齐行情实体（幂等：已注册的 provider 跳过）。"""
    from src.registry import UnknownProvider, get
    from src.strategy_framework.md_gateway import list_md_gateway_providers

    for provider in list_md_gateway_providers():
        try:
            get(DOMAIN_MARKET, provider)
        except UnknownProvider:
            try:
                register(DOMAIN_MARKET, provider, LegacyMarketPlugin(provider))
            except ValueError:      # 并发补齐：另一线程已注册同一键（同形实体，幂等）
                pass
