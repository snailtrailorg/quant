"""D32 端口 · 非结构化研究（batch 172a）。

研究域独立于 A03 的 DataKind：`research` 是端口能力（`PORT_CAPS`），
不进 `CAPABILITIES`。领域模型（Document/Chunk/Claim）在 P6 才落地，
本批只定端口签名。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..quant_common.domain import Health


@runtime_checkable
class ResearchPort(Protocol):
    def fetch(self, req: Any) -> list[Any]: ...
    def search(self, query: str, filters: dict) -> list[Any]: ...
    def health(self) -> Health: ...
