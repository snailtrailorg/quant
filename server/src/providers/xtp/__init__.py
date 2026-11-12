"""XTP provider 插件（import 即注册；本包导入期零 vnpy）。"""
from __future__ import annotations

from src.registry import DOMAIN_TRADING, register

from .plugin import XtpPlugin

register(DOMAIN_TRADING, "xtp", XtpPlugin())

__all__ = ["XtpPlugin"]
