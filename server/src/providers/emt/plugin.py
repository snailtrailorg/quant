"""EMT 插件入口（D32 P4）：role 视图构建。导入期零 vnpy/emt SDK——惰导入留 build 内。"""
from __future__ import annotations

from typing import Any, Callable

from src.registry import UnsupportedCapability

from .manifest import Manifest


class EmtPlugin:
    manifest = Manifest()

    def build(self, account: Any, role: str, slot: Any, *,
              event_engine: Any = None, guard_factory: Callable | None = None,
              setting: dict | None = None) -> Any:
        """role='td' → ExecutionPort（EmtAdapter 薄壳：兼 gw/td_api）；MD 仍走 legacy（P4 范围仅 TD）。"""
        if role == "td":
            if event_engine is None:
                raise ValueError("EmtPlugin.build(td) 需要 event_engine")
            # 惰导入：adapters 会连带 vnpy/emt SDK——只在真正构建运行时导入（守「import providers 不导 vnpy」）
            from src.strategy_framework.adapters import EmtAdapter
            tcid = (int(getattr(slot, "tid", 20)) - 1) % 100 + 20
            adapter = EmtAdapter(event_engine=event_engine,
                                 order_prefix=getattr(slot, "order_prefix", ""),
                                 td_client_id=tcid)
            if setting:
                if getattr(slot, "window_open", False):
                    adapter.connect(setting)          # Login fail-fast → 入口 78
                else:
                    adapter.remember_login(setting)   # 窗关启动，等窗开沿
            return adapter
        raise UnsupportedCapability(f"EMT 暂不支持 role={role!r}（MD 走 legacy md_gateway）")
