"""D32 端口 · 账户（batch 172a）。

只暴露健康与授权态。凭证解析不在此（共享层负责，见 D32 §3.1）。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..quant_common.domain import Health


@runtime_checkable
class AccountPort(Protocol):
    def health(self) -> Health: ...
    def entitlements(self) -> Any: ...
