"""XTP 插件入口：registry 注册 + role 视图构建（D32 §9.3）。"""
from __future__ import annotations

from typing import Any, Callable

from src.registry import UnsupportedCapability

from .manifest import Manifest


class XtpPlugin:
    manifest = Manifest()

    def build(self, account: Any, role: str, slot: Any, *,
              event_engine: Any = None, guard_factory: Callable | None = None,
              setting: dict | None = None) -> Any:
        """role='td' → ExecutionPort；role='md' → MarketDataPort（待 MD 迁移）。"""
        if role == "td":
            from . import td
            if event_engine is None:
                raise ValueError("XtpPlugin.build(td) 需要 event_engine")
            guard = guard_factory or (lambda name: (lambda fn: fn))
            return td.build_td(event_engine, slot, setting or {}, guard)
        if role == "md":
            from . import md
            return md.build_md(account, slot)
        raise UnsupportedCapability(f"XTP 不支持 role={role!r}")
