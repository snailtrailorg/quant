"""接口运行态读侧（M6a 状态只读卡）：按 code 读进程心跳，不判活、不写。

心跳键族（C4）：md → `iface:hb:md:<md_code>`、td → `iface:hb:td:<td_code>`（Redis Hash，
HeartbeatWriter 写，TTL=90s）。本模块只做"键 → 展示字段"的投影：
- running：键在且 TTL>0（进程活着并在续心跳）
- working：心跳里 working=1（enabled 且已连接并在干活）
- connected：心跳里 connected=1
存活判定（告警）仍归 health_monitor，此处不做阈值判断。
"""
from __future__ import annotations

from src.quant_common.redis_client import business_redis


def _key(kind: str, code: str) -> str:
    if kind not in ("md", "td"):
        raise ValueError(f"未知接口类型 {kind!r}")
    return f"iface:hb:{kind}:{code}"


def _flag(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes")


def status_of(kind: str, codes: list[str]) -> list[dict]:
    """给一批 code 出运行态（缺失键=running False，不算错）。"""
    try:
        r = business_redis(decode_responses=True, socket_timeout=3)
    except Exception as e:  # noqa: BLE001 —— 观测面不可用不阻断页面
        return [{"code": c, "running": False, "working": False, "connected": False,
                 "enabled": None, "error": f"redis: {e}"} for c in codes]
    out: list[dict] = []
    for code in codes:
        k = _key(kind, code)
        try:
            h = r.hgetall(k) or {}
            ttl = r.ttl(k)
        except Exception as e:  # noqa: BLE001
            out.append({"code": code, "running": False, "working": False,
                        "connected": False, "enabled": None, "error": str(e)})
            continue
        running = bool(h) and ttl != -2
        out.append({
            "code": code,
            "running": running,
            "working": _flag(h.get("working", 0)),
            "connected": _flag(h.get("connected", 0)),
            "enabled": None if "enabled" not in h else _flag(h.get("enabled")),
            "ts": h.get("ts"),
            "provider": h.get("provider", ""),
        })
    return out
