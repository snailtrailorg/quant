"""批 4b worker 迁骨架接线矩阵测试：11 项钩子 each 有接线且 period 对。

覆盖（设计 v2.1「钩子全清单」表逐行）：
- 项 1 xread 流消费 = XReadSleeper 注入 EngineLoop.sleeper；
- 项 2/6/7/9/11 周期钩子（stop-check/heartbeat/snapshot/factor-recalc/zombie-claim）；
- 项 4/5/8/10 步进钩子（sess-edge/blind-watch/halt-edge/td-reconnect，period=0）；
- 项 3 看门狗+事件线程 = EngineLoop 内建（watchdog/event_engines/on_fatal）；
- 行为级：停止路径（清理+exit 0，不用 failure=exit）/ 心跳 D3 七字段+ts / 时段沿清基线 /
  盲视门 / TD 重连沿 / 僵尸认领（认领即消费）。
"""
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.strategy_runner.hub_worker as hw
from src.strategy_framework.runtime.loop import EngineLoop
from src.strategy_framework.runtime.xsleeper import XReadSleeper

STREAM = "hub:bars:X.SHSE"
HB_TASK_KEY = "quant:hb:task:41001"


class FakeStreamRedis:
    """接线期所需最小流存储（不阻塞——loop.run 被 mock，sleeper 不真读）。"""

    def __init__(self):
        self.stream = []            # [(id, fields)]
        self.kv = {}
        self.hashes = {}            # key -> 最后一次 hset mapping（心跳断言）
        self.groups = set()
        self.group_del_calls = []
        self.xack_calls = []
        self.autoclaim = ("0-0", [])
        self.exists_val = 0

    def xgroup_destroy(self, s, g):
        self.groups.discard(g)

    def xgroup_del(self, s, g):
        self.group_del_calls.append(g)

    def xgroup_create(self, s, g, id="$", mkstream=False):
        self.groups.add(g)

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = v

    def exists(self, k):
        return self.exists_val

    def xrevrange(self, s, count=240):
        return list(reversed(self.stream[-count:]))

    def hset(self, key, mapping=None, **kw):
        m = dict(mapping or {})
        m.update(kw)
        self.hashes[key] = m

    def expire(self, key, ttl):
        pass

    def xautoclaim(self, s, g, c, min_idle_time=0, count=20):
        return self.autoclaim

    def xack(self, s, g, *ids):
        self.xack_calls.append(ids)


class StubStrategy:
    def __init__(self):
        self.bars = []

    def on_bar(self, bar, history):
        self.bars.append(bar)
        return SimpleNamespace(action=None)


class StubAdapter:
    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._vt2cid = {}

    def query_account(self):
        return []

    def query_position(self):
        return []

    def query_orders(self):
        return []

    def query_trades(self):
        return []


def _bar_fields(seq=1, ts="2026-08-27T10:01:00"):
    return {"gen": 1, "seq": seq, "ts": ts, "pub_ts": time.time(),
            "open": "10", "high": "10", "low": "10", "close": "10", "volume": "100"}


@pytest.fixture
def wired():
    """起真 run() 至 loop.run 前（run 打桩捕获实例），返回接线面。

    in_astock_session 在 run() 内 from-import 绑定——patch 源模块后钩子闭包共用同一
    mock，行为测试随后改 return_value 驱动时段序列（E-1 教训：闭包晚绑定必须 patch 源）。
    """
    fake = FakeStreamRedis()
    strategy = StubStrategy()
    td = SimpleNamespace(connect_status=True)
    ee = MagicMock()
    ee._thread = None
    ctx = {
        "tid": 41001, "sid": "smoke-strat", "symbol": "X.SHSE",
        "strategy": strategy, "adapter": StubAdapter(), "event_engine": ee,
        "td_api": td, "history": [], "frozen": {"now": False, "sticky": False},
        "warmup_pg": lambda: [], "stop_check": lambda: False,
        "reconcile": MagicMock(), "account_id": "t1",
    }
    captured = {}

    def _fake_run(self, stop_after_iterations=0):
        captured["loop"] = self

    sess_mock = MagicMock(return_value=True)
    with patch.object(hw, "_valkey", lambda: fake), \
         patch("src.strategy_framework.runtime.alerts.safe_notify"), \
         patch("src.quant_common.session.in_astock_session", sess_mock), \
         patch.object(EngineLoop, "run", _fake_run), \
         patch("src.strategy_runner.hub_worker.os._exit") as exit_mock:
        hw.run(ctx)
        exit_mock.reset_mock()   # run() finally 的收尾 exit(0)/xgroup_del 已发生（mock 不真退）——
        fake.group_del_calls.clear()   # 行为断言从零起算
        yield {"fake": fake, "ctx": ctx, "strategy": strategy, "td": td, "ee": ee,
               "loop": captured["loop"], "sess_mock": sess_mock, "exit_mock": exit_mock}


def _hooks(w):
    return {h.name: h for h in w["loop"]._hooks}


class TestWiringMatrix:
    """11 项接线对照（设计「钩子全清单」表；验收：each 有 loop.every 注册+period 对）。"""

    EXPECT = {   # 名 -> period（秒）；0=步进
        "stop-check": 5.0, "sess-edge": 0.0, "blind-watch": 0.0, "heartbeat": 5.0,
        "snapshot": 60.0, "halt-edge": 0.0, "factor-recalc": 5.0,
        "td-reconnect": 0.0, "zombie-claim": 5.0,
    }

    def test_nine_every_hooks_exact(self, wired):
        """项 2/4/5/6/7/8/9/10/11：九个 every 注册，名字与 period 逐一锁定（多缺错皆红）。"""
        hooks = {h.name: h.period for h in wired["loop"]._hooks}
        assert hooks == self.EXPECT

    def test_sleeper_injected(self, wired):
        """项 1：流消费经 XReadSleeper 注入（旧 run() 本体 xreadgroup 位）。"""
        s = wired["loop"]._sleep
        assert isinstance(s, XReadSleeper)
        assert s._stream == STREAM and s._group == "task-41001"
        assert s._consumer == f"w-{__import__('os').getpid()}"

    def test_preflight_builtin(self, wired):
        """项 3：看门狗+事件线程存活=骨架内建（替换旧 timer 段 sd_notify/et.is_alive 检查）。"""
        loop = wired["loop"]
        assert loop._watchdog is not None
        assert loop._event_engines == (wired["ee"],)
        assert loop._on_fatal is not None          # D3：on_fatal 告警接线（exit 1 前先告警）
        assert loop._fatal_exit_code == 1 and loop.step == 5.0
        assert loop.name == "live-task-41001"

    def test_hook_names_unique(self, wired):
        names = [h.name for h in wired["loop"]._hooks]
        assert len(names) == len(set(names))


class TestStopPath:
    """停止路径（设计裁定）：_stop_hook 内 finally 等价清理 + os._exit(0)；不用 failure=exit。"""

    def test_not_due_no_exit(self, wired):
        _hooks(wired)["stop-check"].fn()   # ctx stop_check=False
        wired["exit_mock"].assert_not_called()
        assert wired["fake"].group_del_calls == []

    def test_due_cleans_and_exits_zero(self, wired):
        wired["ctx"]["stop_check"] = lambda: True
        _hooks(wired)["stop-check"].fn()
        wired["exit_mock"].assert_called_once_with(0)   # 正常停止码：Restart=on-failure 不拉起
        assert wired["fake"].group_del_calls == ["task-41001"]


class TestHeartbeatD3:
    """D3 定案：worker 只写自有 7 字段+ts；direct 专属字段（ticks/sess_ticks/last_tick_ts）不写。"""

    def test_fields_exactly_seven_plus_ts(self, wired):
        _hooks(wired)["heartbeat"].fn()
        m = wired["fake"].hashes[HB_TASK_KEY]
        assert set(m) == {"pid", "md", "gen", "last_bar_ts", "lag", "bars", "frozen", "ts"}
        assert m["md"] == "hub" and m["frozen"] == "0"
        assert not ({"ticks", "sess_ticks", "last_tick_ts"} & set(m))


class TestSessEdgeAndBlindWatch:
    def test_zombie_claim_processes_bar(self, wired):
        """项 11 行为：认领即消费（on_bar 驱动 + XACK）——幂等靠 ts 去重。"""
        wired["fake"].autoclaim = ("1-1", [("1-1", _bar_fields(seq=1))])
        _hooks(wired)["zombie-claim"].fn()
        assert len(wired["strategy"].bars) == 1
        assert wired["fake"].xack_calls

    def test_blind_watch_hub_alive_fresh_bar_not_frozen(self, wired):
        w = wired
        w["fake"].autoclaim = ("1-1", [("1-1", _bar_fields(seq=1))])
        _hooks(w)["zombie-claim"].fn()          # 盘中 bar → sess_bar_wall=now
        w["fake"].exists_val = 1                # hub 心跳在
        _hooks(w)["blind-watch"].fn()
        assert w["ctx"]["frozen"]["now"] is False

    def test_blind_watch_hub_dead_freezes(self, wired):
        w = wired
        w["fake"].autoclaim = ("1-1", [("1-1", _bar_fields(seq=1))])
        _hooks(w)["zombie-claim"].fn()
        w["fake"].exists_val = 0                # hub 心跳丢失
        _hooks(w)["blind-watch"].fn()
        assert w["ctx"]["frozen"]["now"] is True

    def test_sess_edge_clears_stale_baseline(self, wired):
        """项 4 行为：时段进入沿清 sess_bar_wall 基线——旧基线不再喂断流判定。"""
        w = wired
        w["fake"].autoclaim = ("1-1", [("1-1", _bar_fields(seq=1))])
        _hooks(w)["zombie-claim"].fn()          # 盘中 bar → 基线=now
        base = time.time()
        with patch("time.time", return_value=base + 400):   # bar 停更 >300s
            _hooks(w)["blind-watch"].fn()
            assert w["ctx"]["frozen"]["now"] is True        # 停更腿生效
        w["sess_mock"].return_value = False
        _hooks(w)["sess-edge"].fn()             # 出沿
        w["sess_mock"].return_value = True
        _hooks(w)["sess-edge"].fn()             # 进沿 → 基线清零
        w["fake"].exists_val = 1
        with patch("time.time", return_value=base + 800):
            _hooks(w)["blind-watch"].fn()
            assert w["ctx"]["frozen"]["now"] is False       # 无基线=不算停更


class TestTdReconnect:
    def test_edge_fires_reconcile_once(self, wired):
        w = wired
        h = _hooks(w)["td-reconnect"]
        h.fn()                                   # True→True 无沿
        w["td"].connect_status = False
        h.fn()                                   # 掉线（无沿：初始 was=True）
        w["td"].connect_status = True
        h.fn()                                   # 重连沿 → 对账（runner 超集，含成交补录）
        w["ctx"]["reconcile"].assert_called_once()
