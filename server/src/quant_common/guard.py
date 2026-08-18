"""handler 守卫与 systemd 通知（从 strategy_runner.main 归位，2026-08-19）。

P-F2：本模块**不含告警**（alert 依赖 alert_notify→data_platform，会让 quant_common 上行）。
需要"异常时告警"的调用方用回调注入：`guard(name, alert=my_alert_fn)`。
"""
from __future__ import annotations
import logging
import os

logger = logging.getLogger("quant_common")


def guard(name: str, alert=None):
    """handler 包装：任何异常只记日志不上抛（vnpy 事件线程零保护，F-26——
    一次异常=线程静默死亡=永久失聪）。alert 回调可选（自带 try 包裹）。

    Args:
        name: 日志定位名（如 "hub.on_tick"）
        alert: Callable[title, body] —— 异常时的告警钩子（由调用方注入，本包不依赖告警层）
    """
    def deco(fn):
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.exception("handler %s 异常（已拦截，事件线程存活）", name)
                if alert is not None:
                    try:
                        alert(f"handler 异常: {name}", "事件已跳过，进程继续。详见 journalctl。")
                    except Exception:
                        pass  # 守卫绝不放行任何异常（纵深防御）
        return wrapped
    return deco


def sd_notify(msg: str) -> None:
    """systemd notify（喂 WATCHDOG 看门狗）。无 NOTIFY_SOCKET（本地/手工运行）时静默跳过。"""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        import socket
        if addr.startswith("@"):
            addr = "\0" + addr[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
    except Exception:
        pass  # 喂狗失败不杀主流程（systemd 会重启，靠 Restart 兜底）
