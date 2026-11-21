"""EMT provider 插件（import 即注册；本包导入期零 vnpy/emt SDK）。"""
from __future__ import annotations

from src.registry import DOMAIN_TRADING, register

from .plugin import EmtPlugin

register(DOMAIN_TRADING, "emt_emq", EmtPlugin())

__all__ = ["EmtPlugin"]
