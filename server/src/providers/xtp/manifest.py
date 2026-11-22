"""XTP 插件清单（声明式；registry.register 校验 port_caps/id/domain）。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Manifest:
    id: str = "xtp"
    domain: str = "trade_interface"
    # 交易实体只声明交易域端口；行情能力属另一个实体（market_interface）。
    port_caps: frozenset[str] = frozenset({"execution", "account"})
    data_kinds: tuple[str, ...] = ("hist_quote", "rt_quote")
    # terminal_id 可用域（D32 §9.5：TD 2..99；MD 账号预留号 1）
    terminal_domains: dict = field(default_factory=lambda: {"td": (2, 99), "md": (1, 1)})
