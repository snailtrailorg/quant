#!/usr/bin/env python3
"""批 4b G2 冒烟道具：内存流（JournalRedis）+ stub TD 起真 worker 进程（hub_worker.run 本体）。

三断言（设计 v2.1 验收 4——批 2 曾因缺真机冒烟判 P1 的教训不复犯）：
1. 钩子分发节奏——XReadSleeper 双节奏：bar xadd→on_bar 延迟 ≤2.5s（block ≤500ms 必返）；
   5s 心跳节拍（相邻 hset 间隔 3~8s）；
2. 心跳 D3 七字段+ts（pid/md/gen/last_bar_ts/lag/bars/frozen/ts——无 direct 专属字段）；
3. 停止路径——stop_check 到点 → 子进程 exit 0 且 xgroup_del 清理留痕；NOGROUP → exit 75。

自包含：不依赖真 Valkey/PG/vnpy（vnpy 缺席时注入 EVENT_TRADE 常量 stub；TD/adapter 全 stub；
RiskControl 直读的 VALKEY_URL 指向拒绝端口防拖慢）。证据经 JSONL 文件跨进程传递。

用法：cd server && venv/bin/python scripts/run_worker_smoke.py [--duration 15]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

HB_TASK_KEY = "quant:hb:task:41001"
STREAM = "hub:bars:SMOKE.SHSE"


# ── 内存流存储（子进程内；证据落盘）───────────────────────────────────────────
class JournalRedis:
    """worker 所需 Valkey 流/键命令的最小实现；每次调用追加 JSONL 证据（崩溃安全：逐条 flush）。"""

    def __init__(self, path: str, nogroup: bool = False):
        self._path = path
        self._nogroup = nogroup
        self._cond = threading.Condition()
        self._seq = 0
        self.stream: list[tuple[str, dict]] = []
        self.kv: dict = {}
        self.hashes: dict = {}
        self.groups: dict = {}      # gname -> last_delivered_id
        self.exists_val = 0

    def _log(self, op: str, **kw):
        rec = {"t": time.time(), "op": op}
        rec.update(kw)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @staticmethod
    def _num(eid: str) -> int:
        return int(eid.split("-")[0])

    def xadd(self, stream, fields, **kw):
        with self._cond:
            self._seq += 1
            eid = f"{self._seq}-1"
            self.stream.append((eid, dict(fields)))
            self._log("xadd", id=eid, ts=fields.get("ts"))
            self._cond.notify_all()

    def _drain(self, gname: str, count: int):
        if gname not in self.groups:
            raise RuntimeError(f"NOGROUP No such key '{STREAM}' or group '{gname}'")
        last = self._num(self.groups[gname])
        out = [(eid, f) for eid, f in self.stream if self._num(eid) > last][:count]
        if out:
            self.groups[gname] = out[-1][0]
        return out

    def xreadgroup(self, group, consumer, streams, count=10, block=0):
        if self._nogroup:
            raise RuntimeError(f"NOGROUP No such key '{STREAM}' or group '{group}'")
        with self._cond:
            entries = self._drain(group, count)
            # 注：xsleeper 已钳 1ms 永不产 block=0（双盲 B P1 修）——BLOCK 0 的真实
            # 永久阻塞语义由 test_xsleeper 钳位断言结构性排除，道具无需模拟
            if not entries and block > 0:
                self._cond.wait(block / 1000.0)
                entries = self._drain(group, count)
        if entries:
            self._log("xreadgroup", n=len(entries))
        return [(STREAM, entries)] if entries else []

    def xgroup_destroy(self, s, g):
        self.groups.pop(g, None)

    def xgroup_del(self, s, g):
        self.groups.pop(g, None)
        self._log("xgroup_del", group=g)

    def xgroup_create(self, s, g, id="$", mkstream=False):
        self.groups[g] = self.stream[-1][0] if (self.stream and id == "$") else "0-0"

    def xrevrange(self, s, count=240):
        return list(reversed(self.stream[-count:]))

    def xautoclaim(self, s, g, c, min_idle_time=0, count=20):
        return "0-0", []

    def xack(self, s, g, *ids):
        self._log("xack", n=len(ids))
        return len(ids)

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = v

    def exists(self, k):
        return self.exists_val

    def hset(self, key, mapping=None, **kw):
        m = dict(mapping or {})
        m.update(kw)
        self.hashes[key] = m
        self._log("hset", key=key, fields=sorted(m))

    def expire(self, key, ttl):
        pass

    def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.hashes.pop(k, None)


# ── 子进程：真 worker 本体 + stub 装配 ───────────────────────────────────────
def child(mode: str, evidence: str, duration: float) -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    os.environ["VALKEY_URL"] = "redis://127.0.0.1:1/0"   # RiskControl 直读即时拒绝（halt-edge 兜底）
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        import vnpy  # noqa: F401
    except ImportError:   # vnpy 缺席环境：run() 只需 EVENT_TRADE 常量
        import types
        for name, attrs in [("vnpy", {}), ("vnpy.trader", {}), ("vnpy.trader.event", {"EVENT_TRADE": "eTrade"})]:
            sys.modules.setdefault(name, types.ModuleType(name))
        for k, v in sys.modules["vnpy.trader.event"].__dict__.items():
            if k.startswith("EVENT"):
                setattr(sys.modules["vnpy.trader.event"], k, v)

    from unittest.mock import patch
    import src.strategy_runner.hub_worker as hw

    r = JournalRedis(evidence, nogroup=(mode == "nogroup"))

    class StubStrategy:
        def on_bar(self, bar, history):
            r._log("on_bar", ts=bar["ts"])
            return SimpleNamespace(action=None)

    class StubAdapter:
        _lock = threading.Lock()
        _vt2cid = {}
        def query_account(self):
            r._log("query_account")
            return []
        def query_position(self):
            return []
        def query_orders(self):
            return []
        def query_trades(self):
            return []

    t0 = time.time()
    ctx = {
        "tid": 41001, "sid": "smoke-strat", "symbol": "SMOKE.SHSE",
        "strategy": StubStrategy(), "adapter": StubAdapter(),
        "event_engine": SimpleNamespace(register=lambda *a, **k: None, _thread=None),
        "td_api": SimpleNamespace(connect_status=True),
        "history": [], "frozen": {"now": False, "sticky": False},
        "warmup_pg": lambda: [],
        "stop_check": lambda: time.time() - t0 > duration,
        "reconcile": lambda: r._log("reconcile"),
        "account_id": "smoke",
    }

    def publish():   # harness 侧发布线程（worker 本体仍单线程——禁后台线程约束不涉 harness）
        base = datetime.now().replace(second=0, microsecond=0)
        i = 0
        while time.time() - t0 < duration - 2:
            time.sleep(1.0)
            i += 1
            r.xadd(STREAM, {"gen": 1, "seq": i, "ts": (base + timedelta(minutes=i)).isoformat(),
                            "pub_ts": time.time(), "open": "10", "high": "10", "low": "10",
                            "close": "10", "volume": "100"})

    if mode != "nogroup":
        threading.Thread(target=publish, daemon=True).start()
    with patch.object(hw, "_valkey", lambda: r), \
         patch("src.strategy_framework.runtime.alerts.safe_notify", lambda *a, **k: None):
        hw.run(ctx)   # 真 worker 本体：正常停止/NOGROUP 在钩子/sleeper 内 os._exit 带码退出


# ── 父进程：起子进程、读证据、断言 ───────────────────────────────────────────
def _load(path: str):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _run_child(mode: str, evidence: str, duration: float, workdir: str):
    err = os.path.join(workdir, f"{mode}.stderr.log")
    with open(err, "wb") as ef:
        p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--mode", mode,
                              "--evidence", evidence, "--duration", str(duration)],
                             stdout=ef, stderr=ef, cwd=workdir)
        rc = p.wait(timeout=duration + 60)
    return rc, err


def smoke(duration: float) -> int:
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory(prefix="worker-smoke-") as td:
        # — 场景 A：节奏 + 心跳 + 停止路径（exit 0 + xgroup_del）—
        ev = os.path.join(td, "run.jsonl")
        rc, err = _run_child("run", ev, duration, td)
        print(f"[A] run 子进程退出码={rc}（期望 0）")
        ok &= (rc == 0)
        if ok:
            evs = _load(ev)
            hbs = [e for e in evs if e["op"] == "hset" and e.get("key") == HB_TASK_KEY]
            fields = {f for e in [hbs[-1]] for f in e["fields"]}
            want = {"pid", "md", "gen", "last_bar_ts", "lag", "bars", "frozen", "ts"}
            print(f"[A] 心跳 {len(hbs)} 次，字段={sorted(fields)}")
            ok &= fields == want
            ok &= len(hbs) >= 2
            gaps = [b["t"] - a["t"] for a, b in zip(hbs, hbs[1:])]
            print(f"[A] 心跳间隔={[round(g, 1) for g in gaps]}（期望 3~8s）")
            ok &= all(3.0 <= g <= 8.0 for g in gaps)
            bars = [e for e in evs if e["op"] == "on_bar"]
            adds = {e["ts"]: e["t"] for e in evs if e["op"] == "xadd"}
            lat = [b["t"] - adds[b["ts"]] for b in bars if b["ts"] in adds]
            print(f"[A] on_bar {len(bars)} 根，xadd→消费延迟 max={max(lat):.2f}s（期望 ≤2.5s）")
            ok &= len(bars) >= 3 and max(lat) <= 2.5
            tail = [e["op"] for e in evs[-3:]]
            print(f"[A] 证据尾三事件={tail}（含 xgroup_del=停止清理留痕）")
            ok &= "xgroup_del" in tail
        if not ok:
            print(f"[A] 失败——stderr 见 {err}")
        # — 场景 B：NOGROUP → exit 75 —
        ev2 = os.path.join(td, "nogroup.jsonl")
        rc2, err2 = _run_child("nogroup", ev2, 5.0, td)
        with open(err2, errors="ignore") as f:
            logged = "NOGROUP" in f.read()
        print(f"[B] nogroup 子进程退出码={rc2}（期望 75），日志含 NOGROUP={logged}")
        ok &= (rc2 == 75) and logged
    print("SMOKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["run", "nogroup", "smoke"], default="smoke")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--duration", type=float, default=15.0)
    a = ap.parse_args()
    if a.mode == "smoke":
        sys.exit(smoke(a.duration))
    child(a.mode, a.evidence, a.duration)
