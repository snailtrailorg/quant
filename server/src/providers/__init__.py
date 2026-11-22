"""D32 provider 插件包根（batch 172a）——import 即注册。

新增一个 provider（D32 §9.6）：新建 `providers/<id>/`（manifest+plugin+_transport），
在本文件加一行 `from . import <id>`，再在配置加账号行。核心目录零改动。
"""

from __future__ import annotations

from . import xtp  # noqa: F401  —— import 即注册（P3 交易实体）
from . import emt  # noqa: F401  —— import 即注册（P4 EMT 交易实体，端口 P4c）
from .market_bridge import register_market_bridge

# 行情实体由 md_gateway 注册表投影（过渡桥）——md_gateway 顶层 import vnpy 的形状探测，
# 故**不在此处注册**（保持 import providers 零 vnpy）；运行时由 ensure_registered() 补齐。
# 交易实体：各 provider 包 import 即注册（零 vnpy）。


def ensure_registered() -> None:
    """幂等确保 XTP 已注册（registry.clear 后 / 进程重载场景；启动接线用）。"""
    from src.registry import DOMAIN_TRADING, UnknownProvider, get, register

    try:
        get(DOMAIN_TRADING, "xtp")
    except UnknownProvider:
        register(DOMAIN_TRADING, "xtp", xtp.XtpPlugin())
    try:
        get(DOMAIN_TRADING, "emt_emq")
    except UnknownProvider:
        register(DOMAIN_TRADING, "emt_emq", emt.EmtPlugin())
    # legacy 网关模块先导入（@register_md_gateway 在 import 时登记各 provider 的行情载体）
    try:
        from .xtp import md_gateway  # noqa: F401
    except Exception:   # noqa: BLE001 —— 无 vnpy 环境（单测/离线）不阻断
        pass
    # 行情实体（md_gateway 投影；必须在网关模块导入之后 / registry.clear 后补齐）
    register_market_bridge()


__all__: list[str] = ["xtp", "emt", "ensure_registered", "register_market_bridge"]
