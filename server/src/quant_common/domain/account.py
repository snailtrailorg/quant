"""D32 领域模型 · 资金账户与健康（batch 172a）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AccountState:
    """资金快照。字段对齐 vnpy AccountData 的核心面（不 import vnpy）。"""

    accountid: str
    balance: float = 0.0        # 总权益
    available: float = 0.0      # 可用资金
    frozen: float = 0.0         # 冻结资金
    margin: float = 0.0         # 占用保证金（加密/期货；A 股现货恒 0）


@dataclass(frozen=True)
class Health:
    """统一健康结构。state 用短词（ok/degraded/down），detail 供排障。"""

    ok: bool
    state: str = "ok"
    detail: str = ""

    @classmethod
    def up(cls) -> "Health":
        return cls(ok=True, state="ok")

    @classmethod
    def down(cls, detail: str) -> "Health":
        return cls(ok=False, state="down", detail=detail)
