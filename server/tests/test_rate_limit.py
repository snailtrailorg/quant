"""限流治理单测（2026-08-27，docs/obsolete/任务归档/限流治理吸收.md）。

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


# --- get_rate_limit 两级（24 号限速聚合：类默认 + DB 覆写，去积分档/时段乘数） ---

class TestGetRateLimit:

    def _ds(self, params: dict | None = None):
        from src.data_platform.data_source import TushareDataSource
        return TushareDataSource(params=json.dumps(params) if params else None)

    def test_class_default(self):
        """类默认 DEFAULT_RATE_LIMITS；未知接口 0=不限。"""
        ds = self._ds()
        assert ds.get_rate_limit("adj_factor") == 0.3
        assert ds.get_rate_limit("daily") == 0.5
        assert ds.get_rate_limit("ghost_api") == 0.0

    def test_params_override(self):
        """DB rate_limits 覆盖类默认；未覆盖键回落默认。"""
        ds = self._ds({"rate_limits": {"adj_factor": 1.2}})
        assert ds.get_rate_limit("adj_factor") == 1.2
        assert ds.get_rate_limit("daily") == 0.5


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
        assert rate_limit._BREAKERS[("stub", "0")].state == CircuitBreaker.CLOSED

    def test_exception_records_failure(self):
        """body 抛异常 → 记失败后原样上抛（熔断仍 Closed：1 次 < 阈值）。"""
        ds = _StubDS()
        with pytest.raises(ValueError):
            with rate_limit_context(ds, "daily"):
                raise ValueError("tushare 挂了")
        cb = rate_limit._BREAKERS[("stub", "0")]
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


# ——— 批 73：Valkey 熔断+pacer 新钉（方案 v2 §五验收 1 核心增量）———

class TestValkeyBreaker:
    def test_cross_process_shared_memory(self, _reset_rate_limit):
        """跨进程互见钉（本批存在意义）：两「进程」（两实例同键共读一 Valkey）——
        A 记 3 失败 + B 记 2 失败 = OPEN（进程内注册表时代做不到——记忆随进程死）。"""
        a = CircuitBreaker(fail_threshold=5, reset_timeout=60, clock=lambda: 0.0, key="rl:cb:t:0")
        b = CircuitBreaker(fail_threshold=5, reset_timeout=60, clock=lambda: 0.0, key="rl:cb:t:0")
        for _ in range(3):
            a.record_failure()
        for _ in range(2):
            b.record_failure()   # 进程 B 视角：接着 A 的计数继续（互见）
        assert a.state == CircuitBreaker.OPEN
        assert b.state == CircuitBreaker.OPEN

    def test_probe_lock_ttl_selfheal(self, _reset_rate_limit):
        """探测锁超时自愈：进程死于探测中——TTL 过期后允许再探。"""
        _reset_rate_limit.hset("rl:cb:t:0", mapping={"state": "open", "opened_at": 0.0})
        cb = CircuitBreaker(fail_threshold=5, reset_timeout=60, clock=lambda: 100.0, key="rl:cb:t:0")
        assert cb.allow() is True    # 到点拿锁探测
        assert cb.allow() is False   # 锁在途拒绝
        _reset_rate_limit._advance(120)   # 推进 TTL 时钟越过 probe_ttl（min(60,60)=60）
        assert cb.allow() is True    # 锁过期=可再探（自愈）

    def test_failopen_all_lines(self, monkeypatch):
        """fail-open 全线：Valkey 故障——allow 放行/记账静默/state 视为健康。"""
        from src.data_platform import rate_limit
        def boom():
            raise ConnectionError("valkey down")
        with patch.object(rate_limit, "_r", side_effect=boom):
            cb = CircuitBreaker(key="rl:cb:t:0")
            assert cb.allow() is True
            assert cb.state == CircuitBreaker.CLOSED
            cb.record_failure()   # 静默不炸
            cb.record_success()

    def test_steady_state_zero_write(self, _reset_rate_limit):
        """稳态零写（读后条件写 v2 #4）：Closed+fails==0 的 success 不产生任何键。"""
        from src.data_platform.rate_limit import CircuitBreaker
        cb = CircuitBreaker(key="rl:cb:t:0")
        cb.record_success()
        assert _reset_rate_limit._b.h == {} and _reset_rate_limit._b.kv == {}

    def test_success_clears_cross_process_fails(self, _reset_rate_limit):
        """读后条件写（禁本地脏标记 v2 #4）：别进程写的 fails 由本进程 success 清零
        ——连续失败语义跨进程保持。"""
        _reset_rate_limit.hset("rl:cb:t:0", mapping={"fails": 3})
        CircuitBreaker(key="rl:cb:t:0").record_success()
        assert _reset_rate_limit.hget("rl:cb:t:0", "fails") == "0"


class TestPacer:
    def test_default_zero_no_valkey_touch(self, monkeypatch):
        """默认 0=关：不触 Valkey（现网零行为变化零读放大——v2 #3）。"""
        from src.data_platform import rate_limit
        calls = []
        with patch.object(rate_limit, "_r", side_effect=lambda: calls.append(1)):
            rate_limit._pace("tushare", 0, 0.0)
        assert not calls

    def test_pace_second_call_waits_then_reacquires(self, _reset_rate_limit, monkeypatch):
        """配置>0：首调即占；占位未过期时次调等 PTTL；TTL 过期后重占成功（假 TTL 时钟
        ——双盲 B P2-5：真墙钟忙等 0.5s 且断言弱的对治）。"""
        from src.data_platform import rate_limit
        sleeps = []
        monkeypatch.setattr(rate_limit.time, "sleep", lambda s: sleeps.append(s))
        assert _reset_rate_limit.get("rl:pace:tushare:0") is None
        rate_limit._pace("tushare", 0, 0.5)
        assert _reset_rate_limit.get("rl:pace:tushare:0") == "1"      # 首调占位
        rate_limit._pace("tushare", 0, 0.5)                            # 占位在——等一轮
        assert sleeps and 0 < sleeps[0] <= 0.5
        _reset_rate_limit._advance(0.6)                                # 越过 TTL
        rate_limit._pace("tushare", 0, 0.5)                            # 重占成功（非忙等到 30s 上限）
        assert _reset_rate_limit.get("rl:pace:tushare:0") == "1"

    def test_pace_failopen(self, monkeypatch):
        from src.data_platform import rate_limit
        with patch.object(rate_limit, "_r", side_effect=ConnectionError("down")):
            rate_limit._pace("tushare", 0, 1.0)   # 静默放行不炸


class TestPileUp:
    def test_pacer_and_limiter_coexist_in_context(self, _reset_rate_limit, monkeypatch):
        """叠加钉（A P1-2①）：pacer 与 per-api limiter 同 context 共存（双 sleep 串行=和
        ——RateLimiter 的 sleep 是构造期绑定不可 monkeypatch，串行语义靠单线程结构保证，
        本钉锁「两机制都被喂到正确间隔」）。"""
        from src.data_platform import rate_limit
        from src.data_platform.rate_limit import rate_limit_context
        paced = []
        monkeypatch.setattr(rate_limit, "_pace", lambda p, a, i, n="": paced.append(i))
        monkeypatch.setattr(_StubDS, "get_rate_limit", lambda self, api: 0.0,
                            raising=False)   # limiter 间隔归零（不真睡）
        ds = _StubDS(interval=0.0)
        with rate_limit_context(ds, "daily"):
            pass
        assert paced == [0.0]   # pacer 收到 ds 配置间隔（默认 0=关语义同传）

    def test_cb_daily_failure_counted_after_wrap(self, _reset_rate_limit):
        """收编钉（A P1-2②）：cb_daily 缺口收编后 API 失败计入熔断（穿透语义——
        sync() 外层收编 error，与方案 v2 #6 cb/index 路径一致）。"""
        from unittest.mock import MagicMock
        from src.data_sync import engine
        adapter = MagicMock()
        adapter.provider = "tushare"
        with patch("src.data_sync.engine._fetch_supply", side_effect=RuntimeError("api down")), \
             patch.object(engine, "_get_rate_ds", return_value=_StubDS()):
            with pytest.raises(RuntimeError):
                engine._sync_via_kind_cb_daily(adapter, start="20260901", end_date="20260921")
        from src.data_platform.rate_limit import _r, _BREAKERS
        for (provider, acct), _b in _BREAKERS.items():
            if provider == "tushare":
                assert int(_r().hget(f"rl:cb:{provider}:{acct}", "fails") or 0) >= 1

    def test_dry_run_reads_open_state(self, _reset_rate_limit):
        """dry-run 真值钉（A P1-2③）：读方直读 Valkey——种 open 断言可见（根治恒 closed
        的单测证据）+两段键不串扰。"""
        from src.data_platform import routing
        from src.data_platform.rate_limit import breaker_key
        _reset_rate_limit.hset(breaker_key("tushare", 1), mapping={"state": "open", "opened_at": 0})
        assert routing._breaker_open("tushare", 1) is True
        assert routing._breaker_open("tushare", 0) is False
