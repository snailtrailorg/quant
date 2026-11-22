"""EMT 插件清单（声明式；D32 §9.5 号段：TD 20..119，MD 账号预留号 1）。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Manifest:
    id: str = "emt_emq"
    domain: str = "trade_interface"
    # 端口未落地（P4c 才加 execution）——空集=诚实声明：无载体 → 不建进程、不进下发。
    port_caps: frozenset[str] = frozenset()
    data_kinds: tuple[str, ...] = ("hist_quote", "rt_quote")
    # broker 侧终端号域（EMT SDK 20..119）。与 XTP 2..99 数值有重叠但分属不同柜台；
    # 平台会话槽唯一性由 (login_key, provider, terminal_id) 保证，不依赖号段互斥。
    terminal_domains: dict = field(default_factory=lambda: {"td": (20, 119), "md": (1, 1)})
