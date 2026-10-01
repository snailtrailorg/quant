"""批 76 · F2 冻结响应与自愈——worker 行为钉（真跑 hub_worker.run 的冻结链，无 DB/Valkey）。

**手法**：沿用 `test_hub_worker_migration` 的 wired 骨架（patch `hw._valkey` 假存储 +
`EngineLoop.run` 打桩），消息经 `zombie-claim` 钩子（xautoclaim 认领→process_batch→handle_msg）
驱动；事实表读写以 `patch("src.data_platform.freeze_event.*")` 观察（worker 内是调用期惰性
import ⇒ patch 生效），断言的是**真冰结链的因果**而非替身行为：

1. seq_gap：冻结 → 流衔接（下一根续上）→ **自动解冻**（clear sticky + close(auto_reconnect)）；
2. ts_gap：冻结 → 后续根仍冻（人工档，不自动解）；且事实表记到 gap 秒数；
3. untrusted：冻结 → 人工档；
4. 人工解冻：Valkey 请求键被钩子 GETDEL 消费 → clear sticky + close(manual_*)；
5. 未冻结时的请求=空操作（不写事实、不崩）；
6. 启动补记：run() 起手对上一进程事件记 restart 闭环。
"""
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.strategy_runner.hub_worker as hw
from src.strategy_framework.runtime.loop import EngineLoop

UNFREEZE_KEY = "hub:unfreeze:41001"


class FakeValkey:
    """run() 所需最小存储 + getdel（批 76 解冻请求键消费）。"""

    def __init__(self):
        self.kv, self.hashes, self.groups = {}, {}, set()
        self.autoclaim = ("0-0", [])
        self.exists_val = 0

    def xgroup_destroy(self, s, g):
        self.groups.discard(g)

    def xgroup_del(self, s, g):
        self.groups.discard(g)

    def xgroup_create(self, s, g, id="$", mkstream=False):
        self.groups.add(g)

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, nx=False, ex=None, **kw):
        self.kv[k] = v
        return True

    def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)

    def getdel(self, k):
        return self.kv.pop(k, None)

    def exists(self, k):
        return self.exists_val

    def xrevrange(self, s, count=240):
        return []

    def hset(self, key, mapping=None, **kw):
        m = dict(mapping or {})
        m.update(kw)
        self.hashes[key] = m

    def expire(self, key, ttl):
        pass

    def xautoclaim(self, s, g, c, min_idle_time=0, count=20):
        return self.autoclaim

    def xack(self, s, g, *ids):
        pass


class StubStrategy:
    def on_bar(self, bar, history):
        return SimpleNamespace(action=None)


def _bar(seq, ts):
    return {"gen": 1, "seq": seq, "ts": ts, "pub_ts": time.time(), "account_id": "1",
            "open": "10", "high": "10", "low": "10", "close": "10", "volume": "100"}


@pytest.fixture
def wired():
    fake = FakeValkey()
    ctx = {
        "tid": 41001, "sid": "smoke-strat", "symbol": "X.SHSE",
        "strategy": StubStrategy(), "adapter": SimpleNamespace(), "event_engine": MagicMock(),
        "td_api": SimpleNamespace(connect_status=True), "history": [],
        "frozen": {"now": False, "sticky": False, "sticky_cause": None},
        "warmup_pg": lambda: [], "stop_check": lambda: False,
        "reconcile": MagicMock(), "account_id": 1,
    }
    captured = {}

    def _fake_run(self, stop_after_iterations=0):
        captured["loop"] = self

    with patch.object(hw, "_valkey", lambda: fake), \
         patch("src.strategy_framework.runtime.alerts.safe_notify"), \
         patch("src.quant_common.session.in_session", MagicMock(return_value=True)), \
         patch("src.data_platform.freeze_event.record_freeze") as rec, \
         patch("src.data_platform.freeze_event.close_freeze") as clo, \
         patch.object(EngineLoop, "run", _fake_run), \
         patch("src.strategy_runner.hub_worker.os._exit"):
        hw.run(ctx)
        yield {"fake": fake, "ctx": ctx, "loop": captured["loop"], "rec": rec, "clo": clo}


def _hook(w, name):
    return {h.name: h for h in w["loop"]._hooks}[name]


def _feed(w, *bars):
    """把 bar 经 xautoclaim→process_batch→handle_msg 灌进 worker。"""
    w["fake"].autoclaim = ("1-1", [(f"1-{i}", b) for i, b in enumerate(bars)])
    _hook(w, "zombie-claim").fn()


def test_startup_records_restart_closure(wired):
    """run() 起手：上一进程遗留的进行中事件按 restart 闭环（记账口径，不改冻结态）。"""
    assert any(c.kwargs.get("method") == "restart" for c in wired["clo"].call_args_list), \
        "启动补记 restart 闭环未发生"


def test_seq_gap_freezes_then_auto_unfreezes_on_chain(wired):
    """seq_gap：冻结（记事实）→ 流衔接的下一根 → 自动解冻（close=auto_reconnect）。"""
    frozen = wired["ctx"]["frozen"]
    _feed(wired, _bar(1, "2026-10-01T10:00:00"))
    assert frozen["sticky"] is False                       # 正常根不冻

    _feed(wired, _bar(5, "2026-10-01T10:01:00"))           # seq 跳变=gap
    assert frozen["sticky"] is True and frozen["sticky_cause"] == "seq_gap"
    assert any(c.args[2] == "seq_gap" for c in wired["rec"].call_args_list), "seq_gap 未记事实"

    _feed(wired, _bar(6, "2026-10-01T10:02:00"))           # 续上（seq+1 且 ts 无缺口）
    assert frozen["sticky"] is False and frozen["sticky_cause"] is None
    assert any(c.kwargs.get("method") == "auto_reconnect" for c in wired["clo"].call_args_list), \
        "衔接自动解冻未闭环"


def test_ts_gap_freezes_and_stays_manual(wired):
    """ts 缺口（源侧丢根）：冻结并记 gap 秒数；后续根不复位（人工档，不自动解）。"""
    frozen = wired["ctx"]["frozen"]
    _feed(wired, _bar(1, "2026-10-01T10:00:00"))
    _feed(wired, _bar(2, "2026-10-01T10:06:00"))           # 360s 缺口（段首 2 分外）
    assert frozen["sticky"] is True and frozen["sticky_cause"] == "ts_gap"
    ts_gap_calls = [c for c in wired["rec"].call_args_list if c.args[2] == "ts_gap"]
    assert ts_gap_calls and ts_gap_calls[0].kwargs.get("detail", {}).get("gap_s") == 360

    wired["clo"].reset_mock()
    _feed(wired, _bar(3, "2026-10-01T10:07:00"))
    assert frozen["sticky"] is True and frozen["sticky_cause"] == "ts_gap"   # 仍冻
    assert not wired["clo"].call_args_list, "ts_gap 被自动解冻（人工档被越权自动化）"


def test_untrusted_freezes_and_stays_manual(wired):
    frozen = wired["ctx"]["frozen"]
    bad = _bar(1, "2026-10-01T10:00:00")
    bad["untrusted"] = "1"
    _feed(wired, bad)
    assert frozen["sticky"] is True and frozen["sticky_cause"] == "untrusted"
    assert any(c.args[2] == "untrusted" for c in wired["rec"].call_args_list)

    wired["clo"].reset_mock()
    _feed(wired, _bar(2, "2026-10-01T10:01:00"))
    assert frozen["sticky"] is True
    assert not wired["clo"].call_args_list


def test_manual_unfreeze_consumes_request_key(wired):
    """人工解冻：请求键被 GETDEL 消费 → clear sticky + 事实闭环记 manual_web（带操作者）。"""
    frozen = wired["ctx"]["frozen"]
    frozen.update(sticky=True, sticky_cause="ts_gap")
    wired["fake"].kv[UNFREEZE_KEY] = json.dumps(
        {"operator": "william", "channel": "manual_web", "ts": 1})

    _hook(wired, "unfreeze-poll").fn()

    assert frozen["sticky"] is False and frozen["sticky_cause"] is None
    assert UNFREEZE_KEY not in wired["fake"].kv                    # 原子消费（不留僵尸令）
    clo = [c for c in wired["clo"].call_args_list if c.kwargs.get("method") == "manual_web"]
    assert clo and clo[0].kwargs.get("operator") == "william"


def test_unfreeze_request_when_not_frozen_is_noop(wired):
    """未冻结时的解冻请求：空操作（不写事实、不清无关状态）、键照样消费。"""
    wired["fake"].kv[UNFREEZE_KEY] = json.dumps({"operator": "u", "channel": "manual_im"})
    wired["clo"].reset_mock()          # 清掉 run() 起手的 restart 补记
    _hook(wired, "unfreeze-poll").fn()
    assert not wired["clo"].call_args_list
    assert UNFREEZE_KEY not in wired["fake"].kv


def test_hook_registered_with_five_second_period(wired):
    hooks = {h.name: h.period for h in wired["loop"]._hooks}
    assert hooks.get("unfreeze-poll") == 5.0
