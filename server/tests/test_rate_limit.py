"""限流治理单测（2026-08-27，docs/任务/限流治理吸收.md）。

覆盖：RateLimiter 间隔执行（假时钟）/ get_rate_limit 三级覆盖（含时段乘数方向）/
CircuitBreaker 三态（Closed→Open→Half-open→关/再开）/ rate_limit_context 集成 / 线程安全。
时间全假时钟：RateLimiter/CircuitBreaker 注入 clock/sleep，时段覆盖 patch datetime。
"""
import json
import threading
from datetime import datetime
from unittest.mock import patch

import pytest

from src.data_platform import rate_limit
from src.data_platform.rate_limit import (
    CircuitBreaker, CircuitOpenError, RateLimiter, rate_limit_context)


class _FakeClock:
    """假单调时钟：advance(s) 前进，不自动走。"""

    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture(autouse=True)
def _clean_registries():
    """context 注册表进程级——每测清零防熔断计数跨测污染（conftest 同款兜底）。"""
    rate_limit.reset_registries()
    yield
    rate_limit.reset_registries()


# --- RateLimiter：间隔执行 ---

class TestRateLimiter:

    def test_first_acquire_immediate(self):
        """首次调用立即放行，不等待。"""
        sleeps: list[float] = []
        rl = RateLimiter(interval=0.5, clock=_FakeClock(100.0), sleep=sleeps.append)
        assert rl.acquire("daily") == 0.0
        assert sleeps == []

    def test_second_acquire_waits_remaining(self):
        """第二次调用只等剩余间隔（过了 0.2s → 再等 0.3s）。"""
        clk = _FakeClock(100.0)
        sleeps: list[float] = []
        rl = RateLimiter(interval=0.5, clock=clk, sleep=sleeps.append)
        rl.acquire("daily")
        clk.advance(0.2)
        assert abs(rl.acquire("daily") - 0.3) < 1e-9
        assert len(sleeps) == 1

    def test_no_wait_after_full_interval(self):
        """距上次调用已满间隔 → 不等待。"""
        clk = _FakeClock()
        sleeps: list[float] = []
        rl = RateLimiter(interval=0.5, clock=clk, sleep=sleeps.append)
        rl.acquire("daily")
        clk.advance(0.5)
        assert rl.acquire("daily") == 0.0
        assert sleeps == []

    def test_zero_interval_never_waits(self):
        """间隔 0（不限速 / engine sleep_s=0 兼容路径）永不等待。"""
        sleeps: list[float] = []
        rl = RateLimiter(interval=0, clock=_FakeClock(), sleep=sleeps.append)
        rl.acquire("x")
        rl.acquire("x")
        assert rl.acquire("x") == 0.0
        assert sleeps == []


# --- get_rate_limit 三级覆盖 ---

class TestGetRateLimitThreeLevels:

    def _ds(self, params: dict | None = None):
        from src.data_platform.data_source import TushareDataSource
        return TushareDataSource(params=json.dumps(params) if params else None)

    def test_level1_class_default(self):
        """一级：类级 DEFAULT_RATE_LIMITS；未知接口 0=不限。"""
        ds = self._ds()
        assert ds.get_rate_limit("adj_factor") == 0.3
        assert ds.get_rate_limit("daily") == 0.5
        assert ds.get_rate_limit("ghost_api") == 0.0

    def test_level2_params_override(self):
        """二级：params.rate_limits 覆盖类默认；未覆盖键回落默认。"""
        ds = self._ds({"rate_limits": {"adj_factor": 1.2}})
        assert ds.get_rate_limit("adj_factor") == 1.2
        assert ds.get_rate_limit("daily") == 0.5

    def test_level3_multiplier_shortens_interval(self):
        """三级：multiplier>1=更快=间隔缩短（0.5 / 2 = 0.25，D3 方向）。"""
        ds = self._ds({"rate_time_overrides": [{"window": "00:00-23:59", "multiplier": 2}]})
        assert abs(ds.get_rate_limit("daily") - 0.25) < 1e-9

    def test_level3_multiplier_lt1_lengthens_interval(self):
        """三级反方向：multiplier<1=更慢=间隔拉长（竞价降速 0.5 / 0.5 = 1.0）。"""
        ds = self._ds({"rate_time_overrides": [{"window": "00:00-23:59", "multiplier": 0.5}]})
        assert abs(ds.get_rate_limit("daily") - 1.0) < 1e-9

    def test_level3_window_hit_and_miss(self):
        """窗口 16:00-20:00：17:00 命中（0.5/2.5=0.2），21:00 不命中（原值 0.5）。"""
        ds = self._ds({"rate_time_overrides": [{"window": "16:00-20:00", "multiplier": 2.5}]})
        for hh, expected in [(17, 0.2), (21, 0.5)]:
            with patch("src.data_platform.data_source.datetime") as mdt:
                mdt.now.return_value = datetime(2026, 8, 27, hh, 0)
                assert abs(ds.get_rate_limit("daily") - expected) < 1e-9

    def test_level3_cross_midnight_window(self):
        """跨零点窗口 22:00-02:00：23:10 命中、10:00 不命中。"""
        ds = self._ds({"rate_time_overrides": [{"window": "22:00-02:00", "multiplier": 5}]})
        for hh, mm, expected in [(23, 10, 0.1), (10, 0, 0.5)]:
            with patch("src.data_platform.data_source.datetime") as mdt:
                mdt.now.return_value = datetime(2026, 8, 27, hh, mm)
                assert abs(ds.get_rate_limit("daily") - expected) < 1e-9

    def test_level3_invalid_entries_skipped(self):
        """非法条目（非补零窗口/multiplier≤0/格式错）跳过不崩，合法条目仍生效。"""
        ds = self._ds({"rate_time_overrides": [
            {"window": "9:00-15:00", "multiplier": 2},    # 非两位补零，字符串比较失真
            {"window": "00:00-23:59", "multiplier": 0},   # multiplier 须为正
            {"window": "bad-window", "multiplier": 2},    # 窗口解析失败
            {"window": "00:00-23:59", "multiplier": 4},   # 首条合法命中 → 0.5/4=0.125
        ]})
        assert abs(ds.get_rate_limit("daily") - 0.125) < 1e-9

    def test_level3_zero_interval_untouched(self):
        """不限速（0）不受时段条目影响（无意义也不引入误差）。"""
        ds = self._ds({"rate_time_overrides": [{"window": "00:00-23:59", "multiplier": 2}]})
        assert ds.get_rate_limit("ghost_api") == 0.0

    def test_level3_stacks_on_level2(self):
        """三级叠在二级上：rate_limits 定基础间隔 1.0 → 时段 ÷2 → 0.5。"""
        ds = self._ds({"rate_limits": {"daily": 1.0},
                       "rate_time_overrides": [{"window": "00:00-23:59", "multiplier": 2}]})
        assert abs(ds.get_rate_limit("daily") - 0.5) < 1e-9


# --- CircuitBreaker：三态 ---

class TestCircuitBreaker:

    def test_below_threshold_allows(self):
        """连续失败 <5 仍放行（Closed）。"""
        cb = CircuitBreaker(fail_threshold=5, reset_timeout=60, clock=_FakeClock())
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow() is True

    def test_open_at_threshold(self):
        """连续失败 ≥5 → Open，allow()=False。"""
        cb = CircuitBreaker(fail_threshold=5, reset_timeout=60, clock=_FakeClock())
        for _ in range(5):
            cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert cb.allow() is False

    def test_half_open_after_timeout_single_probe(self):
        """Open 满 60s → Half-open 放一次探测；探测在途其余拒绝。"""
        clk = _FakeClock()
        cb = CircuitBreaker(fail_threshold=5, reset_timeout=60, clock=clk)
        for _ in range(5):
            cb.record_failure()
        clk.advance(59.9)
        assert cb.allow() is False          # 未到点仍 Open
        clk.advance(0.1)                    # 恰 60s
        assert cb.allow() is True           # 本次即探测
        assert cb.state == CircuitBreaker.HALF_OPEN
        assert cb.allow() is False          # 只放一个

    def test_probe_success_closes_and_resets_count(self):
        """半开探测成功 → 关熔断且计数清零（再失败 1 次仍 Closed）。"""
        clk = _FakeClock()
        cb = CircuitBreaker(fail_threshold=5, reset_timeout=60, clock=clk)
        for _ in range(5):
            cb.record_failure()
        clk.advance(60)
        assert cb.allow() is True
        cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED
        cb.record_failure()
        assert cb.allow() is True           # 计数已清零，1 次失败不再开

    def test_probe_failure_reopens_and_recovers_later(self):
        """半开探测失败 → 再 Open；再等 60s 又 Half-open 可恢复。"""
        clk = _FakeClock()
        cb = CircuitBreaker(fail_threshold=5, reset_timeout=60, clock=clk)
        for _ in range(5):
            cb.record_failure()
        clk.advance(60)
        assert cb.allow() is True
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert cb.allow() is False
        clk.advance(60)
        assert cb.allow() is True           # 周而复始可恢复

    def test_success_resets_consecutive_count(self):
        """Closed 态成功清计数：4 败+1 成+4 败仍不开（连续语义）。"""
        cb = CircuitBreaker(fail_threshold=5, reset_timeout=60, clock=_FakeClock())
        for _ in range(4):
            cb.record_failure()
        cb.record_success()
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow() is True


# --- rate_limit_context：集成 ---

class _StubDS:
    """最小 DataSource 替身：context 只需 provider + get_rate_limit。"""

    provider = "stub"

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.calls = 0

    def get_rate_limit(self, api_name: str) -> float:
        self.calls += 1
        return self.interval


class TestRateLimitContext:

    def _seed_limiter(self, interval: float):
        """预置假时钟限速器到注册表（context 内部 setdefault 会复用）。"""
        clk = _FakeClock()
        waits: list[float] = []
        rate_limit._LIMITERS[("stub", "daily")] = RateLimiter(
            interval=interval, clock=clk, sleep=waits.append)
        return clk, waits

    def test_success_paces_second_call(self):
        """成功路径：连续两次 context，第二次等待剩余间隔；熔断保持 Closed。"""
        clk, waits = self._seed_limiter(0.5)
        ds = _StubDS(interval=0.5)
        with rate_limit_context(ds, "daily"):
            pass
        clk.advance(0.1)
        with rate_limit_context(ds, "daily"):
            pass
        assert len(waits) == 1 and abs(waits[0] - 0.4) < 1e-9
        assert rate_limit._BREAKERS["stub"].state == CircuitBreaker.CLOSED

    def test_exception_records_failure(self):
        """body 抛异常 → 记失败后原样上抛（熔断仍 Closed：1 次 < 阈值）。"""
        ds = _StubDS()
        with pytest.raises(ValueError):
            with rate_limit_context(ds, "daily"):
                raise ValueError("tushare 挂了")
        cb = rate_limit._BREAKERS["stub"]
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow() is True

    def test_open_circuit_raises_before_body(self):
        """连续失败 5 次开熔断 → 再进 context 即抛 CircuitOpenError，body 零执行。"""
        ds = _StubDS()
        for _ in range(5):
            with pytest.raises(ValueError):
                with rate_limit_context(ds, "daily"):
                    raise ValueError("boom")
        calls_before = ds.calls
        body_run = False
        with pytest.raises(CircuitOpenError):
            with rate_limit_context(ds, "daily"):
                body_run = True
        assert body_run is False                 # 进 context 即抛，不等间隔不执行
        assert ds.calls == calls_before          # Open 时不再查限速配置（快速失败）

    def test_min_interval_override(self):
        """min_interval 显式覆盖 ds 间隔（engine sleep_s 兼容，0=关闭等待）。"""
        clk, waits = self._seed_limiter(0.5)
        ds = _StubDS(interval=0.5)
        with rate_limit_context(ds, "daily", min_interval=0):
            pass
        clk.advance(0.0)
        with rate_limit_context(ds, "daily", min_interval=0):
            pass
        assert waits == []                       # 两次都立即

    def test_breaker_shared_across_apis(self):
        """D2：熔断按 DataSource 级——不同 api 的失败累积到同一熔断器。"""
        ds = _StubDS()
        for _ in range(4):
            with pytest.raises(ValueError):
                with rate_limit_context(ds, "daily"):
                    raise ValueError("boom")
        with pytest.raises(ValueError):
            with rate_limit_context(ds, "adj_factor"):   # 第 5 次失败换接口也一样开
                raise ValueError("boom")
        with pytest.raises(CircuitOpenError):
            with rate_limit_context(ds, "trade_cal"):
                pass


# --- 线程安全 ---

class TestThreadSafety:

    def test_two_threads_concurrent_acquire_queue_up(self):
        """两线程并发 acquire（时钟静止）：一者立即、一者等满间隔——占位排队不超限。"""
        rl = RateLimiter(interval=10.0, clock=_FakeClock(0.0), sleep=lambda s: None)
        results: list[float] = []
        lock = threading.Lock()

        def worker():
            w = rl.acquire("daily")
            with lock:
                results.append(w)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert sorted(results) == [0.0, 10.0]   # 第二者按占位等满，不踩踏
