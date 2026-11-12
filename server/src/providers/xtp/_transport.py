"""XTP SDK 唯一 import 点（**懒导入**：导入本模块不拉起 vnpy）。

所有 SDK 类/常量/事件都在函数内 import——S1 与「import providers 不导入 vnpy」守门依赖此约束。
"""
from __future__ import annotations

from typing import Any


def td_api_cls() -> Any:
    from vnpy_xtp.gateway.xtp_gateway import XtpTdApi
    return XtpTdApi


def base_gateway_cls() -> Any:
    from vnpy.trader.gateway import BaseGateway
    return BaseGateway


def constants() -> Any:
    from vnpy.trader import constant
    return constant


def objects() -> Any:
    from vnpy.trader import object as obj
    return obj


def events() -> dict[str, str]:
    from vnpy.trader import event
    return {
        "order": event.EVENT_ORDER,
        "trade": event.EVENT_TRADE,
        "position": event.EVENT_POSITION,
        "account": event.EVENT_ACCOUNT,
        "log": event.EVENT_LOG,
    }


def exchange_of(suffix: str) -> Any:
    c = constants()
    mapping = {"SHSE": c.Exchange.SSE, "SSE": c.Exchange.SSE, "SZSE": c.Exchange.SZSE}
    return mapping.get((suffix or "").upper(), c.Exchange.SSE)
