"""SessionCounters + HeartbeatWriter（批 2）。

SessionCounters：时段作用域 tick/bar 计数的单点实现（S6 修订语义）——
时段进沿写 enter_ts（2026-08-25 事故 1「runner 缺沿写入」的单点化根治），
基线沿上清零，僵尸/断流判定收敛到 md_session.zombie_session 纯函数。
并发：事件线程 on_data 写 / 主循环读，GIL 原子（vnxtpmd 全程持 GIL，
2026-08-25 双盲审 .so 实证），沿用裸属性不加锁。

HeartbeatWriter：Valkey 心跳写。超集原则——旧字段名不改只增
（消费方 health_monitor/collector.py 的字段清单锁进测试）。
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from src.strategy_framework.md_session import zombie_session

logger = logging.getLogger("runtime.pulse")


class SessionCounters:
    """时段作用域会话计数器。"""

    def __init__(self):
        self.sess_count = 0
        self.sess_last_ts = 0.0
        self.sess_enter_ts = 0.0
        self._was = False

    # ——— 事件线程侧 ———

    def on_data(self, in_session: bool) -> None:
        """tick/bar 到达。"""
        self.sess_last_ts = time.time()
        if in_session:
            self.sess_count += 1

    # ——— 主循环侧 ———

    def apply_edge(self, in_session: bool) -> bool:
        """时段沿处理（每步调用），返回是否**进入沿**。

        首次调用即在时段内（盘中启动场景）视为进入沿——enter_ts=启动时刻，
        L2 零 tick 宽限从此起算（与 runner 盘中启动语义一致）。
        """
        entered = in_session and not self._was
        if in_session != self._was:
            self.sess_count = 0
            self.sess_last_ts = 0.0
        if entered:
            self.sess_enter_ts = time.time()
        self._was = in_session
        return entered

    def in_session(self) -> bool:
        """当前时段态（沿检测后的缓存值）。"""
        return self._was

    def zombie(self, now: float | None = None, trading_day: bool = True,
               grace: float | None = None) -> bool:
        """僵尸会话判定——委托 md_session.zombie_session 纯函数（唯一实现）。

        grace 透传（双盲审 P1）：AlertPolicy.zombie_grace 必须能真正生效——
        此前不透传恒用默认 600，与 hub 恰好等值纯属巧合（单一来源承诺落空）。
        """
        now = now if now is not None else time.time()
        if grace is None:
            return zombie_session(self._was, self.sess_count, self.sess_enter_ts, now, trading_day)
        return zombie_session(self._was, self.sess_count, self.sess_enter_ts, now, trading_day,
                              grace=grace)

    def stalled(self, now: float | None = None) -> float | None:
        """断流秒数；时段内无任何数据（无基线）时 None。"""
        if not self.sess_last_ts:
            return None
        now = now if now is not None else time.time()
        return now - self.sess_last_ts


class HeartbeatWriter:
    """Valkey 心跳写（hset+expire；失败仅警告——心跳不致命）。"""

    def __init__(self, r, key: str, ttl: int = 90,
                 base: Callable[[], dict] | None = None):
        self._r = r
        self._key = key
        self._ttl = ttl
        self._base = base or (lambda: {})

    def beat(self, **extra) -> None:
        """写一次心跳：base() 静态字段 + extra 动态字段 + ts 兜底。"""
        try:
            fields = dict(self._base())
            fields.update(extra)
            fields.setdefault("ts", time.time())
            self._r.hset(self._key, mapping={k: str(v) for k, v in fields.items()})
            self._r.expire(self._key, self._ttl)
        except Exception as e:
            logger.warning("心跳写失败（不致命）: %s", e)
