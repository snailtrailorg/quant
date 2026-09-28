"""限流 + 熔断（限流治理吸收 2026-08-27，24 号限速抽象聚合）。

三件套：
- RateLimiter：线程安全最小间隔执行器——acquire() 阻塞到距上次调用满间隔
- CircuitBreaker：三态熔断（Closed→Open→Half-open）
- rate_limit_context(ds, api_name)：声明式上下文——进=熔断检查+间隔等待，出=成败入账

设计决策：
- D1 限速在 engine 侧（编排节奏），adapter pull_* 零改动
- D2 熔断按 DataSource 级（配额共享体，任何接口打穿都封整个账号）——非 API 级
- D3 限速策略化（24 号）：RateLimitPolicy 抽象 + FixedIntervalPolicy（类默认 + DB 覆写两级）
  / DailyQuotaPolicy（每日额度）。熔断参数（fail_threshold/reset_timeout）从
  params.circuit_breaker 读，代码默认兜底
"""
from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Callable, Iterator

logger = logging.getLogger("rate_limit")


_R_PID: int = 0
_R_CLI = None


def _r():
    """Valkey 客户端（按 pid 缓存——celery prefork fork 后子进程自动重建连接，父进程复用；
    批 73 双盲 A P2-3：per-call 新建=热路径每命令一次 TCP 建连。socket_timeout=2 短——
    fail-open 快速）。"""
    global _R_PID, _R_CLI
    import redis
    pid = os.getpid()
    if _R_CLI is None or _R_PID != pid:
        _R_CLI = redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
                                      decode_responses=True, socket_timeout=2)
        _R_PID = pid
    return _R_CLI


def breaker_key(provider: str, acct) -> str:
    """熔断状态键单源（批 73 双盲 A P2-7：写方 rate_limit/读方 routing+dry-run 三处共引，
    批 74 键形再动只改这里）。"""
    return f"rl:cb:{provider}:{acct}"


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
    """三态熔断（D2：DataSource 级〔provider×acct，批 73 键升维〕）。

    批 73：状态（state/fails/opened_at）迁 Valkey（HSET key=rl:cb:{provider}:{acct}）——
    跨进程互见（celery prefork+web）+进程回收（--max-tasks-per-child）不丢记忆。
    - 时钟系=epoch 墙钟（monotonic 跨进程不可比；clock 可注入但值域为 epoch）
    - 半开探测锁=SET NX EX（TTL=min(reset_timeout,60)s——进程死于探测中的自愈上限）
    - record_success=读后条件写（HMGET 一次 RTT；稳态零写防 tier1 日循环写放大；
      **禁本地脏标记**——多进程下退化累计失败语义=虚开熔断，v2 #4）
    - fail-open 全线：Valkey 故障=视为健康放行+记账静默（对齐 bulkhead/quota/throttle
      三先例——熔断是保护机制非正确性机制，白板重学成本=fail_threshold 次失败，有界）
    - 竞态声明（v2 裁决 A=单命令原语+最终一致）：达阈值双写幂等；「成功清零 vs 并发
      INCR」交错最坏=漏记晚开一拍（安全侧），不引 Lua
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, fail_threshold: int | None = None, reset_timeout: float | None = None,
                 clock: Callable[[], float] = time.time, ds=None, key: str = ""):
        """ds 有则从 external_interface.params.circuit_breaker 读熔断参数（显式实参 >
        params 配置 > 代码默认兜底）。ds 无 get_param（测试替身）跳过。key=Valkey 状态键
        （空=无共享，进程内也不记忆——仅测试直接构造用）。"""
        if ds is not None:
            gpf = getattr(ds, "get_param_float", None)
            if callable(gpf):
                if fail_threshold is None:
                    fail_threshold = gpf("circuit_breaker", "fail_threshold",
                                         default=5, lo=1, hi=1000)
                if reset_timeout is None:
                    reset_timeout = gpf("circuit_breaker", "reset_timeout",
                                        default=60.0, lo=1, hi=86400)
        self._fail_threshold = max(1, int(fail_threshold if fail_threshold is not None else 5))
        self._reset_timeout = max(1.0, float(reset_timeout if reset_timeout is not None else 60.0))
        # 探测 TTL 与等待窗错位注记（双盲 A P2-6）：拿锁后还要过 pacer（≤30s）+limiter 才到
        # API——reset<60 且 pacer 高间隔时锁可在探测发出前过期→双探（低概率，两探结果都
        # 入账语义收敛），接受不防。
        self._probe_ttl = int(min(self._reset_timeout, 60.0))
        self._clock = clock
        self._key = key

    @property
    def state(self) -> str:
        try:
            v = _r().hget(self._key, "state")
            return v if v else self.CLOSED
        except Exception:
            return self.CLOSED   # fail-open 读侧=视为健康

    def _probe_key(self) -> str:
        return f"{self._key}:probe"

    def allow(self) -> bool:
        """是否放行本次调用。Open 未到 reset_timeout → False；到点/半开窗 → 抢探测锁
        （SET NX EX——防跨进程双探+超时自愈）放行一次探测。"""
        try:
            r = _r()
            state = r.hget(self._key, "state") or self.CLOSED
            if state == self.CLOSED:
                return True
            if state == self.OPEN:
                opened_at = float(r.hget(self._key, "opened_at") or 0)
                if self._clock() - opened_at < self._reset_timeout:
                    return False
            # OPEN 到点 / HALF_OPEN：抢探测锁
            if r.set(self._probe_key(), "1", nx=True, ex=self._probe_ttl):
                r.hset(self._key, mapping={"state": self.HALF_OPEN})   # 半开回写（读侧漂移钉 v2 #12）
                return True
            return False
        except Exception:
            return True   # fail-open：Valkey 故障=放行

    def record_success(self) -> None:
        """成功：关熔断+清计数。读后条件写（非 half_open 且 fails==0 稳态零写）。"""
        try:
            r = _r()
            state, fails = r.hmget(self._key, ("state", "fails"))
            dirty = False
            mapping: dict = {}
            if state is not None and state != self.CLOSED:
                mapping.update(state=self.CLOSED, opened_at=0)
                dirty = True
            if fails is not None and int(fails) > 0:
                mapping.update(fails=0)
                dirty = True
            if dirty:
                r.hset(self._key, mapping=mapping)
                r.delete(self._probe_key())
        except Exception:
            pass   # fail-open：记账失败不阻主流程

    def record_failure(self) -> None:
        """失败：计数+1（HINCRBY 原子）；半开探测失败或达阈值 → Open 重计时+自 GC。"""
        try:
            r = _r()
            fails = r.hincrby(self._key, "fails", 1)
            state = r.hget(self._key, "state")
            # 探测窗 GC 边界（双盲 B P2-1）：探测调用历时>reset 时 hash 已被自 GC（state=None）
            # 而 probe 键仍在=探测在途——本次失败即探测失败，直接再开（对齐进程内语义）
            probe_in_flight = r.exists(self._probe_key())
            if (state == self.HALF_OPEN or (state is None and probe_in_flight)
                    or fails >= self._fail_threshold):
                r.hset(self._key, mapping={"state": self.OPEN,
                                           "opened_at": self._clock()})
                r.delete(self._probe_key())
                r.expire(self._key, int(self._reset_timeout * 2))   # OPEN 态自 GC（closed 稳态零写无键）
                logger.warning("熔断打开：连续失败 %d 次（阈值 %d，%.0fs 后半开探测）",
                               fails, self._fail_threshold, self._reset_timeout)
        except Exception:
            pass


# 注册表：熔断=参数缓存（状态在 Valkey，键 (provider, acct)——批 73 键升维：值=ds.interface_id
# 批 55a 已带，裸构造 None→0；批 74 多账号时 adapter 按 acct 构造 ds 即自动分键零迁移）；
# limiter=进程内（分期裁决 B：双 worker 失真 2 倍可忍 vs 熔断完全失效——键形
# rl:lim:{provider}:{api}:{acct} 已预留平滑过渡）
_LIMITERS: dict[tuple[str, str], RateLimiter] = {}   # (provider, api_name) -> 限速器
_BREAKERS: dict[tuple[str, str], CircuitBreaker] = {}   # (provider, acct) -> 熔断器（参数缓存+Valkey 键）
_REGISTRY_LOCK = threading.Lock()


def reset_registries() -> None:
    """清空注册表（测试隔离用；运行期勿调——会丢限速占位；熔断记忆在 Valkey 不受影响）。"""
    with _REGISTRY_LOCK:
        _LIMITERS.clear()
        _BREAKERS.clear()


def _get_pace_interval(ds) -> float:
    """provider 总闸间隔（params.pacer.min_interval，默认 0=关——机制落地+配置驱动开启，
    批 74 多源激活时按 provider 配真值）。取参在 breaker.allow() 之后（Open 快速失败不触
    配置读）。lo=0 保 default=0 过钳位=「0=关」语义；_StubDS 无方法跳过（v2 #11）。"""
    gpf = getattr(ds, "get_param_float", None)
    if not callable(gpf):
        return 0.0
    try:
        # hi=30 与 _pace 的 30s 等待上限对齐（双盲 B P2-6：配 >30s 每调用必告警+放行=总闸失效）
        return max(0.0, float(gpf("pacer", "min_interval", default=0.0, lo=0, hi=30)))
    except (TypeError, ValueError):
        return 0.0


def _pace(provider: str, acct, interval: float, api_name: str = "") -> None:
    """provider 级总闸（批 73）：跨 API 键的供应商节奏上限——SET NX PX 占位+PTTL 重试。

    与 per-api limiter 叠加（双 sleep 最坏=和，同线程串行可接受——docstring 声明）；
    补 DailyQuotaPolicy「无聚合硬顶」缺口。interval<=0 短路不触 Valkey（现网默认零行为
    变化零读放大）。fail-open：Valkey 故障=pacer 消失。30s 等待上限（节奏问题不该挂更久）。"""
    if interval <= 0:
        return
    key = f"rl:pace:{provider}:{acct}"
    deadline = time.monotonic() + 30.0
    while True:
        try:
            if _r().set(key, "1", nx=True, px=int(interval * 1000)):
                return
            ttl = _r().pttl(key)
        except Exception:
            return   # fail-open
        wait = (ttl / 1000.0) if (ttl and ttl > 0) else 0.01
        if time.monotonic() + wait > deadline:
            logger.warning("pacer 等待超限（30s）放行——interval=%.1f 过大或 Valkey TTL 异常 [%s]",
                           interval, api_name)
            return
        logger.debug("pacer 等待 %s %.3fs", api_name or "?", wait)
        time.sleep(wait)


@contextmanager
def rate_limit_context(ds, api_name: str,
                       min_interval: float | None = None) -> Iterator[None]:
    """声明式限流+熔断+pacer：with rate_limit_context(ds, "daily"): pull_daily(...)。

    - 进：熔断检查（Open→raise CircuitOpenError）→ pacer 总闸（默认 0=关）→ per-api 间隔等待
    - 出：正常返回记 record_success；异常穿透记 record_failure 后原样上抛
    - 间隔从 ds.get_rate_limit(api_name) 三级取；min_interval 显式覆盖
    - 归因铁律（v2 #2）：context 只包 API 调用语句、DB 写一律在外——整函数包裹会让
      内层吞掉的 API 异常记成 success + DB 失败错打数据源熔断（2026-09-03 根治的反模式）
    """
    provider = getattr(ds, "provider", type(ds).__name__)
    # 降级键分叉注记（双盲 A P2-5）：DB 读失败回落裸构造 TushareDataSource 时
    # interface_id=None→acct=0，与真行键 {id} 分叉——该窗口熔断记忆孤岛化（丢记忆方向，
    # 安全侧）；批 74 单源构造 ds 时根治。
    acct = getattr(ds, "interface_id", None) or 0
    bkey = (provider, str(acct))
    with _REGISTRY_LOCK:
        if bkey not in _BREAKERS:   # 首次构造从 ds.params.circuit_breaker 读熔断参数
            _BREAKERS[bkey] = CircuitBreaker(ds=ds, key=breaker_key(provider, acct))
        breaker = _BREAKERS[bkey]
        limiter = _LIMITERS.setdefault((provider, api_name), RateLimiter())
    if not breaker.allow():
        raise CircuitOpenError(
            f"数据源 {provider} 熔断打开中（连续失败达阈值，稍后半开探测）——本轮跳过")
    _pace(provider, acct, _get_pace_interval(ds), api_name)
    interval = (float(min_interval) if min_interval is not None
                else ds.get_rate_limit(api_name))
    limiter.acquire(api_name, interval)
    try:
        yield
    except Exception:
        breaker.record_failure()
        raise
    breaker.record_success()


class RateLimitPolicy(ABC):
    """限速策略（24 号限速抽象聚合）：不同平台不同限速模型，抽象统一接口。

    Tushare=间隔秒（FixedIntervalPolicy）；聚宽/米筐=每日额度（DailyQuotaPolicy）。
    engine 只依赖 get_interval(api_name)，不关心具体平台怎么算间隔。
    """
    @abstractmethod
    def get_interval(self, api_name: str) -> float:
        """两次调用最小间隔秒。0=不限。"""


class FixedIntervalPolicy(RateLimitPolicy):
    """固定间隔策略（Tushare）：api_name -> 间隔秒，两级（类默认 + DB 覆写）。

    非法覆写值回落类默认（同原 get_rate_limit 容错语义——非法值不崩、回落最保守默认）。
    """
    def __init__(self, defaults: dict[str, float], overrides: dict[str, float]):
        self._defaults = defaults or {}
        self._overrides = overrides or {}

    def get_interval(self, api_name: str) -> float:
        if api_name in self._overrides:
            try:
                return max(0.0, float(self._overrides[api_name]))
            except (TypeError, ValueError):
                pass   # 非法覆写回落类默认
        try:
            return max(0.0, float(self._defaults.get(api_name, 0.0)))
        except (TypeError, ValueError):
            return 0.0


class DailyQuotaPolicy(RateLimitPolicy):
    """每日额度策略（聚宽/米筐）：每日 N 次 → 平均间隔 86400/N。

    注（盲审 B-P1）：平均间隔是「下界近似」——防超不防 burst（under-utilize），
    且无「日上限硬顶」，持续跑超一天会超 N 次。日上限硬顶 + 当日计数留真接批补。
    """
    def __init__(self, daily_quota: int):
        try:
            self._quota = max(1, int(daily_quota))
        except (TypeError, ValueError):
            self._quota = 1   # 非法回落最保守（1 次/天，盲审 B-P2-3）

    def get_interval(self, api_name: str) -> float:
        return 86400.0 / self._quota
