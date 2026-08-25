"""EngineLoop：到期驱动钩子循环（批 2）。

替代三引擎各自的 `while True + sleep(N) + counter%N`：
- 每钩子独立周期、独立异常策略（failure=log 续行 / exit 进程域退出）；
- 睡眠时长=距最近到期（上限 step）——worker 的阻塞消费（xreadgroup）经 sleeper
  注入实现双节奏（批 3），定时钩子不可能被繁忙流饿死；
- 看门狗与事件线程存活检查内建（替换三份复制，R-BR12/F-26 单一实现）。

可测性：`now`（时钟）与 `sleeper`（睡眠）皆可注入——假时钟确定性测试零真实等待。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

logger = logging.getLogger("runtime.loop")


@dataclass
class Hook:
    name: str
    period: float                       # 秒；0=每步
    fn: Callable[[], None]
    failure: Literal["log", "exit"] = "log"
    next_due: float = field(default=0.0, init=False)


class EngineLoop:
    """到期驱动主循环。`every()` 注册钩子，`run()` 永续执行。"""

    def __init__(self, *, name: str, step: float = 5.0,
                 sleeper: Callable[[float], None] | None = None,
                 now: Callable[[], float] | None = None,
                 watchdog: Callable[[], None] | None = None,
                 event_engines: tuple = (),
                 on_fatal: Callable[[str], None] | None = None,
                 fatal_exit_code: int = 1):
        self.name = name
        self.step = step
        self._sleep = sleeper or time.sleep
        self._now = now or time.monotonic
        self._watchdog = watchdog
        self._event_engines = tuple(e for e in event_engines if e is not None)
        self._on_fatal = on_fatal
        self._fatal_exit_code = fatal_exit_code
        self._hooks: list[Hook] = []

    def every(self, name: str, period: float, fn: Callable[[], None],
              failure: Literal["log", "exit"] = "log") -> None:
        """注册周期钩子（period=0 每步执行）。重名拒绝（防静默覆盖）。"""
        if any(h.name == name for h in self._hooks):
            raise ValueError(f"hook 重名: {name}")
        h = Hook(name=name, period=period, fn=fn, failure=failure)
        h.next_due = self._now()
        self._hooks.append(h)

    # ——— 内部（测试可直接驱动）———

    def _next_wait(self) -> float:
        """距最近到期（上限 step）；全为每步钩子时=step。"""
        now = self._now()
        due = [h.next_due for h in self._hooks if h.period > 0]
        if not due:
            return self.step
        return max(0.0, min(min(due) - now, self.step))

    def _fatal(self, reason: str) -> None:
        """进程域退出：on_fatal 告警（双盲审 P1——Restart=always 单元 OnFailure 不触发，
        首报告警不能等 StartLimit 撞满）→ critical → os._exit。"""
        logger.critical("[%s] %s，退出待 systemd 重启", self.name, reason)
        if self._on_fatal is not None:
            try:
                self._on_fatal(reason)
            except Exception as e:
                logger.warning("[%s] on_fatal 回调失败: %s", self.name, e)
        os._exit(self._fatal_exit_code)

    def _preflight(self) -> None:
        """每步前置：喂狗 + 事件线程存活。"""
        if self._watchdog is not None:
            try:
                self._watchdog()
            except Exception as e:
                logger.warning("[%s] 看门狗喂失败（继续）: %s", self.name, e)
        for ee in self._event_engines:
            t = getattr(ee, "_thread", None)
            if t is not None and not t.is_alive():
                self._fatal("EventEngine 事件线程已死亡")

    def _dispatch(self) -> None:
        now = self._now()
        for h in self._hooks:
            if h.period > 0 and now < h.next_due:
                continue
            try:
                h.fn()
            except Exception as e:
                if h.failure == "exit":
                    self._fatal(f"钩子 {h.name} 失败（failure=exit）: {e}")
                logger.warning("[%s] 钩子 %s 失败（继续）: %s", self.name, h.name, e)
            if h.period > 0:
                h.next_due = now + h.period

    def run(self, stop_after_iterations: int = 0) -> None:
        """永续循环；stop_after_iterations>0 供测试有界执行。"""
        i = 0
        while True:
            self._sleep(self._next_wait())
            self._preflight()
            self._dispatch()
            i += 1
            if stop_after_iterations and i >= stop_after_iterations:
                return
