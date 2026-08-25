"""SubscriptionManager（批 2）：订阅期望集 diff + 全量幂等重放。

收编 hub _sync_subscriptions 语义（2026-08-20 生命周期闭环 + 补盲审 S1 修正）：
- poll：diff 增删（先加后退，hub 原序）；
- replay：全量幂等重放，**先退 removed** 再全量订阅——XTP 重连不恢复订阅 +
  启动竞态双兜底；落在重放窗口内的移除若不先退将永不被 SDK 退订（订阅泄漏）；
- on_reconnect_edge：重连沿强制 replay。

纯逻辑不持周期（15s diff / 60s replay 的节奏由引擎 loop.every 注册——
到期驱动废 counter%N 相位耦合）。单标的回调异常只记日志（周期幂等重放兜底），
期望集读取失败沿用旧集（hub「读真相源失败」语义）。
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger("runtime.subs")


class SubscriptionManager:
    """订阅管理：desired() 给期望集真相，subscribe/unsubscribe 给后端动作。"""

    def __init__(self, desired: Callable[[], set[str]],
                 subscribe: Callable[[str], None],
                 unsubscribe: Callable[[str], None]):
        self._desired = desired
        self._subscribe_fn = subscribe
        self._unsubscribe_fn = unsubscribe
        self._subscribed: set[str] = set()

    @property
    def current(self) -> set[str]:
        """当前已同步集（拷贝；心跳 subs 计数等只读用）。"""
        return set(self._subscribed)

    def poll(self) -> None:
        """增量同步：diff 出增删各自动作（hub 非 replay 分支语义，先加后退）。"""
        want = self._want()
        if want is None:
            return
        added = want - self._subscribed
        removed = self._subscribed - want
        if not added and not removed:
            return
        for s in sorted(added):
            self._call(self._subscribe_fn, "订阅", s)
        for s in sorted(removed):
            self._call(self._unsubscribe_fn, "退订", s)
        logger.info("订阅同步：+%d -%d（共 %d）", len(added), len(removed), len(want))
        self._subscribed = want

    def replay(self) -> None:
        """全量幂等重放：**先退 removed** 再全量订阅（hub replay 分支语义原样）。"""
        want = self._want()
        if want is None:
            return
        removed = self._subscribed - want
        for s in sorted(removed):
            self._call(self._unsubscribe_fn, "退订", s)   # 重连场景通常空集（XTP 侧已清零），幂等无害
        if want != self._subscribed:
            logger.info("订阅重放（共 %d，退 %d）", len(want), len(removed))
        for s in sorted(want):
            self._call(self._subscribe_fn, "订阅", s)
        self._subscribed = want

    def on_reconnect_edge(self) -> None:
        """重连沿：强制全量重放（XTP 重连不恢复订阅）。"""
        logger.info("MD 重连沿：重放全部订阅")
        self.replay()

    # ——— 内部 ———

    def _want(self) -> set[str] | None:
        """读期望集；真相源读失败沿用旧集（返回 None 表示本轮跳过）。"""
        try:
            return set(self._desired())
        except Exception as e:
            logger.warning("读订阅真相源失败（沿用旧集）: %s", e)
            return None

    @staticmethod
    def _call(fn: Callable[[str], None], action: str, sym: str) -> None:
        try:
            fn(sym)
        except Exception as e:
            logger.warning("%s失败 %s: %s", action, sym, e)
