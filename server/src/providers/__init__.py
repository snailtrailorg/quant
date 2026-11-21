"""D32 provider 插件包根（batch 172a）——import 即注册。

新增一个 provider（D32 §9.6）：新建 `providers/<id>/`（manifest+plugin+_transport），
在本文件加一行 `from . import <id>`，再在配置加账号行。核心目录零改动。
"""

from __future__ import annotations

from . import xtp  # noqa: F401  —— import 即注册（P3）
from . import emt  # noqa: F401  —— import 即注册（P4 EMT 执行插件）


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
    # MD 网关注册（vnpy 依赖，运行时才导入；保证 create_md_gateway('xtp') 可用）
    try:
        from .xtp import md_gateway  # noqa: F401
    except Exception:   # noqa: BLE001 —— 无 vnpy 环境（单测/离线）不阻断
        pass


__all__: list[str] = ["xtp", "emt", "ensure_registered"]
