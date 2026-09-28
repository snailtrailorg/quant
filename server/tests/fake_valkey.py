"""FakeValkey：rate_limit 熔断/pacer 的测试替身（批 73）。

覆盖原语集：hset/hget/hmget/hincrby/set(nx/ex)/pttl/delete/expire/get。
- TTL 语义：懒过期（读写时检查 expire_at，内部时钟可 _advance 推进——探测锁自愈钉用）
- conftest autouse 注入（patch rate_limit._r）——每测试独立实例=状态天然隔离；
  跨进程钉：两实例共享同一 backend dict 模拟（`FakeValkey.shared(a, b)`）
- 与 test_routing._RedisMock 的关系：那是 bulkhead 旧 mock（incr/decr/expire 子集），
  本件为 rl:* 全原语——后续可合流，本批不扩大
"""
import time


class _Backend:
    def __init__(self):
        self.h: dict[str, dict[str, str]] = {}
        self.kv: dict[str, str] = {}
        self.exp: dict[str, float] = {}   # key -> expire_at（内部时钟系）

    def now(self) -> float:
        return time.time() + getattr(self, "_offset", 0.0)

    def alive(self, key: str) -> bool:
        exp = self.exp.get(key)
        return exp is None or self.now() < exp


class FakeValkey:
    def __init__(self, backend: _Backend | None = None):
        self._b = backend or _Backend()

    def _advance(self, seconds: float) -> None:
        """测试钩子：推进内部 TTL 时钟（探测锁自愈/OPEN 到点钉）。"""
        self._b._offset = getattr(self._b, "_offset", 0.0) + seconds

    # —— hash ——
    def hset(self, key, mapping=None, **kw):
        m = mapping or kw
        if not self._b.alive(key):   # resurrect：过期键重写=新键无 TTL（对齐真 Redis，双盲 B P2-4）
            self._b.h.pop(key, None)
            self._b.exp.pop(key, None)
        self._b.h.setdefault(key, {}).update({k: str(v) for k, v in m.items()})

    def hget(self, key, field):
        if not self._b.alive(key):
            self._b.h.pop(key, None)
            return None
        return self._b.h.get(key, {}).get(field)

    def hmget(self, key, fields):
        return [self.hget(key, f) for f in fields]

    def hincrby(self, key, field, amount=1):
        cur = self.hget(key, field)
        v = (int(cur) if cur is not None else 0) + amount
        self._b.h.setdefault(key, {})[field] = str(v)
        return v

    # —— string ——
    def set(self, key, value, nx=False, ex=None, px=None):
        if not self._b.alive(key):
            self._b.kv.pop(key, None)
        if nx and key in self._b.kv:
            return None
        self._b.kv[key] = str(value)
        if ex is not None:
            self._b.exp[key] = self._b.now() + float(ex)
        elif px is not None:
            self._b.exp[key] = self._b.now() + float(px) / 1000.0
        return True

    def get(self, key):
        if not self._b.alive(key):
            self._b.kv.pop(key, None)
            return None
        return self._b.kv.get(key)

    def pttl(self, key) -> int:
        if key not in self._b.kv:
            return -2
        exp = self._b.exp.get(key)
        if exp is None:
            return -1
        ms = int((exp - self._b.now()) * 1000)
        return ms if ms > 0 else -2

    def exists(self, key) -> bool:
        alive_kv = self._b.alive(key) and key in self._b.kv
        alive_h = self._b.alive(key) and key in self._b.h
        if not alive_kv:
            self._b.kv.pop(key, None)
        if not alive_h:
            self._b.h.pop(key, None)
        return alive_kv or alive_h

    def delete(self, *keys):
        n = 0
        for k in keys:
            for store in (self._b.kv, self._b.h, self._b.exp):
                if k in store:
                    store.pop(k, None)
                    n += 1
        return n

    def expire(self, key, seconds: int):
        if key in self._b.kv or key in self._b.h:
            self._b.exp[key] = self._b.now() + float(seconds)
            return True
        return False
