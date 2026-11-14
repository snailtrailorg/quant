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
    """项目 vt 后缀/别名 → vnpy Exchange 枚举（批175e：别名归一见 symbology，本处只保留
    "vt → vnpy 枚举"出口层——vnpy 沪市枚举名恰为 SSE，属出口语义而非入站别名）。"""
    from src.quant_common.symbology import vt_suffix
    c = constants()
    mapping = {"SHSE": c.Exchange.SSE, "SZSE": c.Exchange.SZSE, "BSE": c.Exchange.BSE}
    try:
        return mapping.get(vt_suffix(suffix or ""), c.Exchange.SSE)
    except ValueError:
        return c.Exchange.SSE
