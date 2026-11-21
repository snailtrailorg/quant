"""EMT 插件清单（声明式；D32 §9.5 号段：TD 20..119，MD 账号预留号 1）。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Manifest:
    id: str = "emt_emq"
    domain: str = "trading_account"
    port_caps: frozenset[str] = frozenset({"execution", "market_data", "account"})
    data_kinds: tuple[str, ...] = ("hist_quote", "rt_quote")
    # terminal_id 可用域（EMT TD 20..119——与 XTP 2..99 不交叠，同账号两 provider 不撞）
    terminal_domains: dict = field(default_factory=lambda: {"td": (20, 119), "md": (1, 1)})
