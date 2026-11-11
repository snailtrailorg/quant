"""D32 端口 · 历史数据（batch 172a）。

这是 A03 `DataBus` 的**薄门面**，不是第二套拉取契约：入参 `DataRequest`、
出参 `ContractFrame` 均属 A03（`docs/architecture/A03-数据供给总线设计.md` §5.1）。
窗口/地板/游标归 A03，插件只做无状态区间抓取。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..quant_common.contract import ContractFrame, DataRequest


@runtime_checkable
class HistoryPort(Protocol):
    def fetch(self, req: DataRequest) -> ContractFrame:
        """A03 的 `DataRequest` -> `ContractFrame`（两者同属层 0，无反向耦合）。"""
        ...
