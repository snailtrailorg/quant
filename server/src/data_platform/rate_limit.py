"""限流 + 熔断（限流治理吸收 2026-08-27，docs/任务/限流治理吸收.md）。

三件套：
- RateLimiter：线程安全最小间隔执行器——acquire() 阻塞到距上次调用满间隔
- CircuitBreaker：三态熔断（Closed→Open→Half-open）
- rate_limit_context(ds, api_name)：声明式上下文——进=熔断检查+间隔等待，出=成败入账

设计决策（任务文件 §设计决策）：
- D1 限速在 engine 侧（编排节奏），adapter pull_* 零改动
- D2 熔断按 DataSource 级（Tushare 配额共享体，任何接口打穿都封整个账号）——非 API 级
- D3 间隔三级覆盖（DataSource.get_rate_limit）：类默认 → params.rate_limits →
  params.rate_time_overrides 时段乘数（interval /= multiplier，>1=更快=间隔缩短）
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator

logger = logging.getLogger("rate_limit")


class CircuitOpenError(RuntimeError):
    """熔断打开（数据源连续失败达阈值）——engine 捕获跳过本轮，不重试不打爆。"""


class RateLimiter:
    """线程安全最小间隔执行器：同一调用流相邻两次 acquire 至少间隔 interval 秒。

    首次 acquire 立即放行；后续按「上次占位时刻 + 间隔」排队——占位而非记 now，
    并发第二者排在其后（两线程同抢：一者立即、一者等满间隔，不踩踏）。
    clock/sleep 可注入（测试假时钟，任务文件 §mock 方式）。
    """

    def __init__(self, interval: float = 0.0,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._interval = max(0.0, float(interval))
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last: float | None = None   # 上次占位时刻（monotonic 系）

    def set_interval(self, interval: float) -> None:
        """更新间隔（时段覆盖随时段变化，context 每次刷新；非法值按 0=不限）。"""
        try:
            self._interval = max(0.0, float(interval))
        except (TypeError, ValueError):
            self._interval = 0.0

    def acquire(self, api_name: str = "", interval: float | None = None) -> float:
        """阻塞等待直至距上次调用满最小间隔，返回实际等待秒（首次=0 不等待）。

        api_name 仅用于日志；interval 显式传入则覆盖当前间隔（context 每次取三级现值）。
        """
        if interval is not None:
            self.set_interval(interval)
        with self._lock:
            now = self._clock()
            if self._last is None:
                self._last = now
                wait = 0.0
            else:
                ready_at = self._last + self._interval
                wait = max(0.0, ready_at - now)
                self._last = max(ready_at, now)   # 占位：并发调用依次排队
        if wait > 0:
            logger.debug("限速等待 %s %.3fs", api_name or "?", wait)
            self._sleep(wait)
        return wait


class CircuitBreaker:
    """三态熔断（D2：DataSource 级）。Closed --连续失败≥阈值--> Open
    --reset_timeout 到点--> Half-open（只放一次探测）--> 成功回 Closed / 失败再 Open。

    所有状态转移持锁（线程安全）；clock 可注入（测试假时钟）。
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, fail_threshold: int = 5, reset_timeout: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self._fail_threshold = fail_threshold
        self._reset_timeout = reset_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._state = self.CLOSED
        self._fails = 0
        self._opened_at = 0.0
        self._probing = False   # Half-open 探测在途（只放一个，防探测期打爆）

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def allow(self) -> bool:
        """是否放行本次调用。Open 未到 reset_timeout → False；到点转 Half-open 放一次探测。"""
        with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                if self._clock() - self._opened_at < self._reset_timeout:
                    return False
                self._state = self.HALF_OPEN   # 到点：本次调用即探测
                self._probing = True
                return True
            if self._probing:                  # Half-open：探测在途，其余拒绝
                return False
            self._probing = True
            return True

    def record_success(self) -> None:
        """成功：关熔断，连续失败计数清零。"""
        with self._lock:
            self._state = self.CLOSED
            self._fails = 0
            self._probing = False

    def record_failure(self) -> None:
        """失败：计数+1；Half-open 探测失败或连续失败达阈值 → Open 重新计时。"""
        with self._lock:
            self._fails += 1
            if self._state == self.HALF_OPEN or self._fails >= self._fail_threshold:
                self._state = self.OPEN
                self._opened_at = self._clock()
                self._probing = False
                logger.warning("熔断打开：连续失败 %d 次（阈值 %d，%.0fs 后半开探测）",
                               self._fails, self._fail_threshold, self._reset_timeout)


# 进程内注册表（任务 §限定范围：单实例内存计数足够，不做分布式）
_LIMITERS: dict[tuple[str, str], RateLimiter] = {}   # (provider, api_name) -> 限速器
_BREAKERS: dict[str, CircuitBreaker] = {}            # provider -> 熔断器（D2 实例级）
_REGISTRY_LOCK = threading.Lock()


def reset_registries() -> None:
    """清空注册表（测试隔离用；运行期勿调——会丢熔断记忆）。"""
    with _REGISTRY_LOCK:
        _LIMITERS.clear()
        _BREAKERS.clear()


@contextmanager
def rate_limit_context(ds, api_name: str,
                       min_interval: float | None = None) -> Iterator[None]:
    """声明式限流+熔断：with rate_limit_context(ds, "daily"): pull_daily(...)。

    - 进：熔断检查（Open→raise CircuitOpenError，engine 捕获跳过本轮）+ 间隔等待
    - 出：正常返回记 record_success；异常穿透记 record_failure 后原样上抛
    - 间隔从 ds.get_rate_limit(api_name) 三级取（时段覆盖随时段变，每次现取）；
      min_interval 显式覆盖（engine sleep_s 兼容路径，如测试传 0 关闭等待）
    """
    provider = getattr(ds, "provider", type(ds).__name__)
    with _REGISTRY_LOCK:
        breaker = _BREAKERS.setdefault(provider, CircuitBreaker())
        limiter = _LIMITERS.setdefault((provider, api_name), RateLimiter())
    if not breaker.allow():
        raise CircuitOpenError(
            f"数据源 {provider} 熔断打开中（连续失败达阈值，稍后半开探测）——本轮跳过")
    interval = (float(min_interval) if min_interval is not None
                else ds.get_rate_limit(api_name))
    limiter.acquire(api_name, interval)
    try:
        yield
    except Exception:
        breaker.record_failure()
        raise
    breaker.record_success()
