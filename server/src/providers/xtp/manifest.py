"""XTP 插件清单（声明式；registry.register 校验 port_caps/id/domain）。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Manifest:
    id: str = "xtp"
    domain: str = "trading_account"
    port_caps: frozenset[str] = frozenset({"execution", "market_data", "account"})
    data_kinds: tuple[str, ...] = ("hist_quote", "rt_quote")
    # terminal_id 可用域（D32 §9.5：TD 2..99；MD 账号预留号 1）
    terminal_domains: dict = field(default_factory=lambda: {"td": (2, 99), "md": (1, 1)})
