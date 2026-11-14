"""批 171s · 平仓同源行情 + Adapter 规则 + 账号专用线程 + 急停审计（守门 S1-S8）。

全部为**行为级**断言（禁 `inspect.getsource` 字符串断言）：驱动真实函数/真实 DB/真实
Valkey，钉「路径不可分辨」的反面。每条守门注释写明「删哪条关键行为会红」——
即突变复测的对照面：

- S1 删同源取价（回退腾讯/成本价）→ TestS1 红；
- S2 主流程写死 "SELL" / 风控只放 SELL → TestS2 红；
- S3 复用旧价（不每轮重取）→ TestS3 两次发单价相同 → 红；
- S4 Gate 只加具名钩子、漏 sleeper/process_batch → TestS4 喂 bar 后 on_bar 被调 → 红；
- S5 删绝对 deadline / no_quote 空轮 / 终局 SSE / uid 定向（退回 0 全站）→ TestS5 红；
- S6 整文件回退（`NOT IN` 语法错）或 vt_orderid 冒充 client_id → TestS6 红；
- S7 持仓源不按 account 过滤/不归一 → TestS7 红；
- S8 缺行不回落/越界不拒 → TestS8 红。
"""
import json
import logging
import os
import threading
import time
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.strategy_framework.adapters import ExecutionAdapter

# 测试专用账号段（远离生产 id；真库用例 finally 清理）
ACC_A = 989901
ACC_B = 989902


# ─────────────────────────── helpers ───────────────────────────

def _pos(sym, volume=100, frozen=0, avg=10.0, direction="direction_long"):
    return SimpleNamespace(symbol=sym, volume=volume, frozen=frozen,
                           avg_price=avg, direction=direction)


def _policy():
    return {"has_limit_band": True, "opponent_depth": 1, "aggression": 0.10,
            "max_order_qty": 100000, "price_tick": 0.001}


def _retry_cfg(**over):
    cfg = {"retry_interval_s": 1, "retry_max": 10, "deadline_s": 120,
           "no_quote_empty_max": 6, "drain_timeout_s": 5}
    cfg.update(over)
    return cfg


def _valkey_up() -> bool:
    try:
        from src.quant_common.redis_client import business_redis
        return bool(business_redis(decode_responses=True, socket_timeout=2).ping())
    except Exception:
        return False


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as c:
            c.execute("SELECT 1 FROM order_log LIMIT 1")
        return True
    except Exception:
        return False


class _Adapter(ExecutionAdapter):
    """最小真实 ExecutionAdapter：定价/S2 规则走基类；持仓可按轮次推进。"""

    def __init__(self, positions=None, position_seq=None):
        self._positions = list(positions or [])
        self._seq = [list(x) for x in position_seq] if position_seq is not None else None
        self.sent = []
        self._orders = []
        self._cid2vt = {}
        self._vt2cid = {}
        self._lock = threading.Lock()
        self._n = 0

    def send_order(self, order):
        self.sent.append(order)
        self._n += 1
        cid = order.client_id or f"auto-{self._n}"
        vt = f"VT.{self._n}"
        with self._lock:
            self._cid2vt[cid] = vt
            self._vt2cid[vt] = cid
        return cid

    def cancel_order(self, order_id):
        return None

    def query_position(self):
        if self._seq is not None:
            return self._seq.pop(0) if self._seq else []
        return list(self._positions)

    def query_orders(self):
        return list(self._orders)

    def get_vt_orderid(self, client_id):
        with self._lock:
            return self._cid2vt.get(client_id)


def _real_rc(halted: bool = True):
    """真 RiskControl（不 mock check_order/is_flat_or_reduce）：只打桩外部数据源。"""
    from src.risk_control.risk import RiskControl
    rc = RiskControl.__new__(RiskControl)
    r = MagicMock()
    r.get.side_effect = lambda k, **kw: ("1" if k == "risk:halted" else None)
    rc._redis = r
    rc._rules_loaded_at = 0
    rc._rules = {
        "global": {"max_drawdown": 0.15, "daily_loss_limit": 0.05},
        "etf_conv": {"max_single_amount": 100000, "max_trades_per_day": 0,
                     "single_position_pct": 0.15},
        "crypto": {"leverage_max": 10, "daily_loss_limit": 0.05,
                   "max_single_amount": 500000},
    }
    rc.is_halted = lambda: halted
    rc.halt_reason = lambda: "test-halt" if halted else None
    rc._maybe_reload_rules = lambda: None
    rc.is_live_trading_allowed = lambda market: True
    rc._get_global_state = lambda aid: SimpleNamespace(
        available=True, total_drawdown=0.0, daily_loss=0.0)
    return rc


# ─────────────────────────── S1 同源 + never-silent ───────────────────────────

class TestS1SameSourceQuote:
    """删同源取价（恢复腾讯 `market_snapshot.get_quote` / 成本价 / 陈 bar）→ 本类红。"""

    @pytest.mark.skipif(not _valkey_up(), reason="Valkey 不可用")
    def test_hub_tick_roundtrip_normalizes_sse_to_shse(self):
        from src.data_platform.hub_quote import get_hub_tick
        from src.quant_common.redis_client import business_redis
        tick = {"last": 12.3, "pre_close": 12.0, "upper_limit": 13.2,
                "lower_limit": 10.8, "bid": [12.29], "ask": [12.31], "ts": "123"}
        r = business_redis(decode_responses=True, socket_timeout=2)
        key = f"hub:latest_tick:{ACC_A}:600000.SHSE"
        try:
            r.set(key, json.dumps(tick), ex=30)
            # 输入 .SSE → 必须按 .SHSE 归一后读到（键逐字符对齐 hub 写入侧）
            assert get_hub_tick(ACC_A, "600000.SSE") == tick
        finally:
            r.delete(key)

    @pytest.mark.skipif(not _valkey_up(), reason="Valkey 不可用")
    def test_quote_price_only_reads_hub_and_never_tencent(self):
        """`_quote_price` 只走 get_hub_tick；腾讯出口命中即 raise（回退=红）。"""
        from src.strategy_runner import trading as T
        from src.quant_common.redis_client import business_redis
        r = business_redis(decode_responses=True, socket_timeout=2)
        key = f"hub:latest_tick:{ACC_A}:600000.SHSE"
        tick = {"last": 12.3, "bid": [12.2], "ask": [12.4]}
        try:
            r.set(key, json.dumps(tick), ex=30)
            with patch("src.data_platform.market_snapshot.get_quote",
                       side_effect=AssertionError("腾讯快照出口必须退役")):
                assert T._quote_price("600000.SSE", ACC_A) == (tick, "")
            r.delete(key)   # 键缺失 → no_tick（仍不得回退腾讯）
            with patch("src.data_platform.market_snapshot.get_quote",
                       side_effect=AssertionError("腾讯快照出口必须退役")):
                assert T._quote_price("600000.SSE", ACC_A) == ({}, "no_tick")
        finally:
            r.delete(key)

    @pytest.mark.skipif(not _valkey_up(), reason="Valkey 不可用")
    def test_unavailable_reason_codes_distinguishable_and_logged(self, caplog):
        """三原因码路径不可混 + 每次失败 warning 留痕（never-silent）。"""
        from src.data_platform.hub_quote import get_hub_tick
        from src.quant_common.redis_client import business_redis
        r = business_redis(decode_responses=True, socket_timeout=2)
        bad_key = f"hub:latest_tick:{ACC_B}:600003.SHSE"
        try:
            r.delete(bad_key)
            with caplog.at_level(logging.WARNING, logger="src.data_platform.hub_quote"):
                assert get_hub_tick(ACC_B, "600002.SSE") == {"_unavailable": "no_tick"}
                r.set(bad_key, "{not-json", ex=30)
                assert get_hub_tick(ACC_B, "600003.SSE") == {"_unavailable": "bad_json"}
                with patch("src.data_platform.hub_quote.business_redis",
                           side_effect=RuntimeError("valkey down")):
                    assert get_hub_tick(ACC_B, "600003.SSE") == {"_unavailable": "valkey_down"}
        finally:
            r.delete(bad_key)
        blob = " ".join(rec.getMessage() for rec in caplog.records)
        assert "no_tick" in blob and "bad_json" in blob and "valkey_down" in blob, \
            "三类不可用必须各自 warning 留痕（静默降级=红）"


# ─────────────────── S2 方向派生 + 定价规则 + 真风控放行 ───────────────────

class TestS2DirectionAndPricing:
    """主流程写死 "SELL" / 风控只放 SELL → 本类红（短仓 BUY 平空）。"""

    def test_adapter_price_rules_matrix(self):
        a = _Adapter([])
        lng = _pos("600000.SHSE")
        sht = _pos("BTCUSDT.BINANCE", direction="direction_short")
        with _patch_rules():
            assert a.close_action(lng) == "SELL"
            assert a.close_action(sht) == "BUY", "direction_short 必须派生 BUY（买回平空）"
            # 有跌停线 → 跌停价（不看对手价）
            assert a.close_reference_price(
                {"lower_limit": 9.87, "bid": [10.0]}, lng, "SELL") == pytest.approx(9.87)
            # 无跌停线、平多 → 买一 ×(1-aggression)
            assert a.close_reference_price(
                {"bid": [10.0], "ask": [10.1]}, lng, "SELL") == pytest.approx(9.0)
            # 无跌停线、平空 → 卖一 ×(1+aggression)（方向感知，非一律打折）
            assert a.close_reference_price(
                {"bid": [10.0], "ask": [10.1]}, sht, "BUY") == pytest.approx(11.11)
            # 三者皆无 → 0（调用方按 no_quote 处理）
            assert a.close_reference_price({}, lng, "SELL") == 0.0

    @pytest.mark.skipif(not _db_up(), reason="真库不可用（S6/S2 审计链）")
    def test_short_position_full_chain_buy_orderlog_and_real_risk(self):
        """构造 direction_short 持仓驱动**整链**：Order.action/order_log.action 均 BUY。"""
        from src.data_platform.db import get_conn
        from src.strategy_runner import trading as T
        pos = _pos("BTCUSDT.BINANCE", volume=1, direction="direction_short")
        adapter = _Adapter(position_seq=[[pos], []])
        rc = _real_rc(halted=True)
        req = "s2" + uuid.uuid4().hex[:8]
        try:
            with patch("src.risk_control.risk.RiskControl.get", return_value=rc), \
                 _patch_rules(), \
                 patch("src.data_platform.hub_quote.get_retry_config", return_value=_retry_cfg()), \
                 patch.object(T, "_quote_price",
                              return_value=({"ask": ["50000"], "bid": ["49900"]}, "")), \
                 patch.object(T, "write_cmd_result"), \
                 patch.object(T, "_alert"), \
                 patch("time.sleep"):
                out = T._close_all_positions(adapter, account_id=None,
                                             operator="u", req_id=req)
            assert out["rejected"] == [] and out["closed_requested"] == 1, \
                "短仓 BUY 平仓必须经真 check_order 放行（风控只放 SELL 则在此被拒）"
            o = adapter.sent[0]
            assert o.action == "BUY", "主流程写死 SELL 必红"
            assert o.price == pytest.approx(50000 * 1.10), "BUY 平空取卖一 ×(1+aggression)"
            assert o.client_id.startswith(f"emc-{req}-")
            with get_conn() as c:
                row = c.execute(
                    "SELECT action, status FROM order_log WHERE client_order_id=%s",
                    (o.client_id,)).fetchone()
            assert row and row[0] == "BUY" and row[1] == "submitted", \
                "order_log.action 必须是派生方向（审计不得写死 SELL）"
        finally:
            with get_conn() as c:
                c.execute("DELETE FROM order_log WHERE client_order_id LIKE %s",
                          (f"emc-{req}-%",))
                c.commit()

    def test_real_risk_passes_emergency_buy_but_rejects_plain_buy(self):
        """真 check_order：halted 下急停 BUY 平空放行、普通 BUY 开仓仍拒（不放松风控）。"""
        rc = _real_rc(halted=True)
        d_flat = rc.check_order(
            {"symbol": "BTCUSDT.BINANCE", "action": "BUY", "volume": 1,
             "price": 50000, "reason": "emergency_close_all"}, account_id=None)
        assert d_flat.approved is True and d_flat.rule != "HALTED"
        d_open = rc.check_order(
            {"symbol": "BTCUSDT.BINANCE", "action": "BUY", "volume": 1,
             "price": 50000, "operator": "u"}, account_id=None)
        assert d_open.approved is False and d_open.rule == "HALTED"


# ─────────────────────────── S3 每轮重取价 ───────────────────────────

class TestS3ReQuoteEveryRound:
    """复用旧价（第二轮不再取 tick）→ 两次发单价相同 → 本类红。"""

    def test_two_retry_rounds_use_different_prices(self):
        from src.strategy_runner import trading as T
        pos = _pos("600000.SHSE", volume=100)
        # 四拍：第1轮初始/复查、第2轮初始/复查（复查空=终局）
        adapter = _Adapter(position_seq=[[pos], [pos], [pos], []])
        adapter.close_reference_price = lambda tick, p, action: float(tick["px"])
        adapter.round_close_price = lambda px, tick: px
        rc = MagicMock()
        rc.check_order.return_value = SimpleNamespace(approved=True, reason="")
        with patch("src.risk_control.risk.RiskControl.get", return_value=rc), \
             _patch_rules(), \
             patch("src.data_platform.hub_quote.get_retry_config", return_value=_retry_cfg()), \
             patch.object(T, "_quote_price",
                          side_effect=[({"px": 10.0}, ""), ({"px": 9.5}, "")]) as q, \
             patch.object(T, "_close_order_open",
                          side_effect=lambda sym, vol, px, *a, **k: (None, "cid")), \
             patch.object(T, "write_cmd_result"), \
             patch.object(T, "_alert"), \
             patch("time.sleep"):
            out = T._close_all_positions(adapter)
        prices = [o.price for o in adapter.sent]
        assert len(prices) == 2 and prices == pytest.approx([10.0, 9.5]), \
            "每轮必须重新取价（复用旧价=两次同价=红）"
        assert q.call_count == 2
        assert out["final_reason"] == "final:done"


# ─────────────────── S4 Gate（两条链）+ 主循环不阻塞 ───────────────────

class _FakeVK:
    def __init__(self):
        self.kv, self.hashes, self.xacks = {}, {}, []

    def xgroup_destroy(self, s, g):
        pass

    def xgroup_create(self, s, g, id="$", mkstream=False):
        pass

    def xgroup_del(self, s, g):
        pass

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, nx=False, ex=None, **kw):
        self.kv[k] = v
        return True

    def delete(self, *ks):
        for k in ks:
            self.kv.pop(k, None)

    def getdel(self, k):
        return self.kv.pop(k, None)

    def exists(self, k):
        return 0

    def xrevrange(self, s, count=240):
        return []

    def hset(self, key, mapping=None, **kw):
        m = dict(mapping or {}); m.update(kw)
        self.hashes[key] = m

    def expire(self, key, ttl):
        pass

    def xautoclaim(self, s, g, c, min_idle_time=0, count=20):
        return self.autoclaim

    def xack(self, s, g, *ids):
        self.xacks.append(tuple(ids))


class _StubStrategy:
    def __init__(self):
        self.on_bar_calls = 0

    def on_bar(self, bar, history):
        self.on_bar_calls += 1
        return SimpleNamespace(action=None)


@pytest.fixture
def wired_worker():
    """真跑 hub_worker.run（EngineLoop.run 打桩）：拿到 loop 钩子与账号内 worker 实例。"""
    import src.strategy_runner.hub_worker as hw
    from src.strategy_framework.runtime.loop import EngineLoop
    fake = _FakeVK()
    strategy = _StubStrategy()
    adapter = MagicMock()
    ctx = {
        "tid": 41002, "sid": "smoke", "symbol": "X.SHSE", "strategy": strategy,
        "adapter": adapter, "event_engine": MagicMock(),
        "td_api": SimpleNamespace(connect_status=True), "history": [],
        "frozen": {"now": False, "sticky": False, "sticky_cause": None},
        "warmup_pg": lambda: [], "stop_check": lambda: False,
        "reconcile": MagicMock(), "account_id": 1, "market": "astock",
        "exec_port": MagicMock(),          # 172b：EVENT_TRADE 端口（必填）
    }
    captured = {}

    def _fake_run(self, *a, **k):
        captured["loop"] = self

    def _capture_start(self):
        captured["worker"] = self

    with patch.object(hw, "_valkey", lambda: fake), \
         patch("src.strategy_framework.runtime.alerts.safe_notify"), \
         patch("src.quant_common.session.in_session", MagicMock(return_value=True)), \
         patch("src.data_platform.freeze_event.record_freeze"), \
         patch("src.data_platform.freeze_event.close_freeze"), \
         patch.object(hw.EmergencyWorker, "start", _capture_start), \
         patch.object(hw.EmergencyWorker, "drain", lambda self, timeout_s: True), \
         patch.object(EngineLoop, "run", _fake_run), \
         patch("src.strategy_runner.hub_worker.os._exit"), \
         patch("src.data_platform.hub_quote.get_retry_config",
               return_value=_retry_cfg()):
        hw.run(ctx)
        yield {"fake": fake, "ctx": ctx, "loop": captured["loop"],
               "worker": captured["worker"], "adapter": adapter,
               "strategy": strategy}


def _hook(env, name):
    return {h.name: h for h in env["loop"]._hooks}[name].fn


def _valid_bar(seq: int = 1, ts: str = "2026-11-10T02:00:00+00:00"):
    """合法 bar（account 匹配、ts 新、pub_ts 新鲜）——Gate 被删时会真驱动 on_bar。"""
    return {"gen": 1, "seq": seq, "ts": ts, "pub_ts": time.time(),
            "account_id": "1", "open": "10", "high": "10", "low": "10",
            "close": "10", "volume": "100"}


class TestS4GateBothChains:
    """Gate 只加具名钩子、漏 `process_batch`（sleeper 链）→ 喂 bar 后 on_bar 被调 → 红。"""

    def test_bar_feed_only_xack_and_named_hooks_skip(self, wired_worker):
        env = wired_worker
        import src.strategy_runner.hub_worker as hw
        from src.strategy_runner import trading as T
        worker = env["worker"]
        worker._closing.clear()
        entered, release = threading.Event(), threading.Event()

        def _blocking(*a, **k):
            entered.set()
            assert release.wait(5), "测试超时：close-all 未被放行"
            return {"pending": [], "no_quote": [], "retrying": False,
                    "closed_requested": 0, "positions_remain": 0,
                    "final_reason": "final:done"}

        th = threading.Thread(
            target=worker._handle,
            args=({"req_id": "g1", "verb": "close-all", "operator": "u",
                   "operator_uid": 7},), daemon=True)
        with patch.object(T, "_close_all_positions", side_effect=_blocking), \
             patch.object(T, "_cancel_working_orders", return_value={}), \
             patch.object(T, "write_cmd_result"), \
             patch("src.quant_common.eventbus.bus.publish_cross_process"), \
             patch.object(hw.time, "sleep"):
            th.start()
            try:
                assert entered.wait(5), "close-all 未进入执行（Gate 未置位）"
                assert worker._closing.is_set()

                # 行情消费链：喂一条 bar（zombie-claim → process_batch）——
                # Gate 漏 sleeper ⇒ on_bar 被调 ⇒ 本断言红
                env["fake"].autoclaim = ("1-1", [("1-1", _valid_bar())])
                _hook(env, "zombie-claim")()
                assert env["strategy"].on_bar_calls == 0, \
                    "急停期 process_batch 必须只 xack（漏 sleeper Gate=红）"
                env["adapter"].send_order.assert_not_called()
                assert ("1-1",) in env["fake"].xacks, "bar 必须被 xack（不丢消息）"

                # 具名钩子链：snapshot/td-reconnect/halt-edge 全跳过 adapter 写
                _hook(env, "snapshot")()
                _hook(env, "halt-edge")()
                _hook(env, "td-reconnect")()
                env["adapter"].query_orders.assert_not_called()

                # 心跳仍写（不整跳），working_orders=-1（未知）
                _hook(env, "heartbeat")()
                hb = env["fake"].hashes["quant:hb:task:41002"]
                assert int(hb["working_orders"]) == -1
                env["adapter"].query_orders.assert_not_called()
            finally:
                release.set()
                th.join(5)
        assert not worker._closing.is_set(), "整轮结束必须清 Gate"


# ───────────────── S5 终止三条件 + 全链可见（零内容定向帧） ─────────────────

class TestS5TerminationAndVisibility:
    """删绝对 deadline / no_quote 空轮 / 终局 SSE / uid 定向（退回 0 全站）→ 本类红。"""

    def test_three_termination_conditions_each_take_effect(self):
        from src.strategy_runner.trading import next_action
        now = 1000.0
        base = {"tries": 0, "empty_streak": 0, "systemic_down": False,
                "pending": ["X:100"], "positions_remain": 1}
        assert next_action({**base, "tries": 3}, now, 3, now + 100, 6) == "final:retries"
        assert next_action({**base, "tries": 1}, now, 10, now, 6) == "final:deadline"
        assert next_action({**base, "empty_streak": 6}, now, 10, now + 100, 6) == "final:no_quote"
        assert next_action({**base, "pending": [], "positions_remain": 0},
                           now, 10, now + 100, 6) == "final:done"
        assert next_action({**base, "systemic_down": True},
                           now, 10, now + 100, 6) == "final:systemic"

    def test_valkey_down_escalates_alert_and_does_not_spin(self):
        from src.strategy_runner import trading as T
        pos = _pos("600000.SHSE")
        adapter = _Adapter(position_seq=[[pos], []])
        rc = MagicMock()
        rc.check_order.return_value = SimpleNamespace(approved=True, reason="")
        with patch("src.risk_control.risk.RiskControl.get", return_value=rc), \
             patch("src.data_platform.hub_quote.get_retry_config", return_value=_retry_cfg()), \
             patch.object(T, "_quote_price", return_value=({}, "valkey_down")) as q, \
             patch.object(T, "write_cmd_result"), \
             patch.object(T, "_alert") as alert, \
             patch("time.sleep"):
            out = T._close_all_positions(adapter, account_id=1)
        assert out["final_reason"] == "final:systemic", "Valkey 全断=直接终局，不空转"
        assert q.call_count == 1, "valkey_down 不得逐轮空转（应一次升级即终局）"
        codes = [c.kwargs.get("code") for c in alert.call_args_list]
        assert "emergency.valkey_down" in codes, "系统性断连必须升级 _alert（留痕非静默）"

    def test_final_sse_uid_targeted_zero_content(self):
        """终局帧必须 uid 定向且 data=={}；uid 缺失/非法/0 → 不发帧（绝不退回 0 全站）。"""
        import src.strategy_runner.hub_worker as HW
        w = HW.EmergencyWorker(adapter=MagicMock(),
                               td_api=SimpleNamespace(connect_status=True),
                               account_id=1, tid="t", closing=threading.Event())
        result = {"pending": [], "no_quote": [], "closed_requested": 1}
        with patch.object(HW.trading, "write_cmd_result") as wr, \
             patch("src.quant_common.eventbus.bus.publish_cross_process") as pub:
            w._final({"req_id": "r1", "verb": "close-all", "operator_uid": 7},
                     result, interrupted=False)
        pub.assert_called_once_with(7, "emergency_close_final", {})
        uid, ev, data = pub.call_args.args
        assert isinstance(uid, int) and uid != 0, "退回 uid=0 全站广播=红（Viewer 越权面）"
        assert data == {}, "帧内塞清单/req_id=红（守批69a/18E 零内容立法）"
        assert any(c.kwargs.get("final") is True for c in wr.call_args_list)
        for bad in (None, "x", 0):
            pub.reset_mock()
            with patch.object(HW.trading, "write_cmd_result"):
                w._final({"req_id": "r2", "verb": "close-all", "operator_uid": bad},
                         result, interrupted=False)
            pub.assert_not_called()


# ─────────────────────── S6 审计认领链（真库逐条） ───────────────────────

class TestS6AuditClaimChain:
    """整文件回退（`NOT IN` 语法错）或 client_id 冒充 vt_orderid → 本类红。"""

    @pytest.mark.skipif(not _db_up(), reason="真库不可用")
    def test_two_chunks_two_rows_and_cid_claim_path(self):
        from src.quant_common.domain import Direction, OrderState, OrderStatus
        from src.data_platform.db import get_conn
        from src.strategy_runner import trading as T
        req = "s6" + uuid.uuid4().hex[:8]
        oid1 = oid2 = None
        try:
            oid1, cid1 = T._close_order_open("600000.SHSE", 100, 10.0, None, "t",
                                             req, 1, "SELL")
            oid2, cid2 = T._close_order_open("600000.SHSE", 200, 10.0, None, "t",
                                             req, 2, "SELL")
            assert oid1 and oid2 and cid1 != cid2, "两笔拆单=两行、client_order_id 各异"
            with get_conn() as c:
                rows = c.execute(
                    "SELECT client_order_id, status FROM order_log "
                    "WHERE client_order_id = ANY(%s) ORDER BY id",
                    ([cid1, cid2],)).fetchall()
            assert len(rows) == 2 and all(r[1] == "submitting" for r in rows)

            adapter = _Adapter([])
            adapter.get_vt_orderid = MagicMock(side_effect=["XTP.9001", None])
            T._close_order_mark(oid1, adapter, cid1, "submitted")
            T._close_order_mark(oid2, adapter, cid2, "submitted")
            with get_conn() as c:
                vts = dict(c.execute(
                    "SELECT client_order_id, vt_orderid FROM order_log "
                    "WHERE client_order_id = ANY(%s)", ([cid1, cid2],)).fetchall())
            assert vts[cid1] == "XTP.9001" and vts[cid2] is None, \
                "get_vt_orderid 返 None 必须写 NULL（不得把 client_id 冒充 vt_orderid）"

            # 路①：vt_orderid 命中
            T.write_order_status(
                OrderState(orderid="", vt_orderid="XTP.9001", symbol="600000.SHSE",
                           direction=Direction.LONG, status=OrderStatus.ALL_TRADED,
                           volume=100, traded=100, price=10.0), adapter, "s6", "600000.SHSE")
            # 路②：vt 行不存在 → _vt2cid → client_order_id 认领（真库 `<> ALL` 执行）
            adapter._vt2cid["CLAIM.2"] = cid2
            T.write_order_status(
                OrderState(orderid="", vt_orderid="CLAIM.2", symbol="600000.SHSE",
                           direction=Direction.LONG, status=OrderStatus.ALL_TRADED,
                           volume=200, traded=200, price=10.0), adapter, "s6", "600000.SHSE")
            with get_conn() as c:
                got = dict(c.execute(
                    "SELECT client_order_id, status FROM order_log "
                    "WHERE client_order_id = ANY(%s)", ([cid1, cid2],)).fetchall())
            assert got[cid1] == "all_traded" and got[cid2] == "all_traded", \
                "两条认领路都必须真回写终态（`<> ALL` 语法错会静默吞）"
        finally:
            if oid1 or oid2:
                with get_conn() as c:
                    c.execute("DELETE FROM order_log WHERE client_order_id LIKE %s",
                              (f"emc-{req}-%",))
                    c.commit()


# ─────────────────────── S7 持仓标的进订阅真相源 ───────────────────────

class TestS7SubscriptionSource:
    """持仓源不按 account 过滤 / 不做 .SSE→.SHSE 归一 → 本类红。"""

    @pytest.mark.skipif(not _db_up(), reason="真库不可用")
    def test_desired_symbols_includes_positions_normalized_and_account_filtered(self):
        import src.md_hub.main as M
        from src.data_platform.db import get_conn
        from src.strategy_framework.runtime.loop import EngineLoop
        from src.strategy_framework.runtime.subs import SubscriptionManager

        acc_a, acc_b = 989910, 989911
        captured = {}

        class _GW:
            provider = "xtp"
            connected = False
            event_engines = []

            def set_on_tick(self, fn):
                pass

            def connect(self, creds, params):
                pass

            def set_context(self, fn):
                pass

            def start_ready(self):
                return False

            def poll_supervise(self, **kw):
                pass

        def _fake_run(self, *a, **k):
            captured["loop"] = self

        try:
            with get_conn() as c:
                for aid in (acc_a, acc_b):
                    c.execute(
                        "INSERT INTO trading_account (id,name,provider,market,capabilities) "
                        "VALUES (%s,%s,'xtp','astock','{}')", (aid, f"s7-{aid}"))
                c.execute(
                    "INSERT INTO position_snapshot (account_id,symbol,direction,volume,frozen) "
                    "VALUES (%s,'689997.SHSE','direction_net',100,0)", (acc_a,))
                c.execute(
                    "INSERT INTO position_snapshot (account_id,symbol,direction,volume,frozen) "
                    "VALUES (%s,'689996.SHSE','direction_net',100,0)", (acc_b,))
                c.commit()
            old = os.environ.get("ACCOUNT_ID")
            os.environ["ACCOUNT_ID"] = str(acc_a)
            try:
                with patch.object(M, "make_valkey", lambda: _FakeVK()), \
                     patch.object(M, "_lease_boot", lambda r, aid: ("uuid", 1)), \
                     patch("src.data_platform.db.verify_schema", return_value=[]), \
                     patch("src.health_monitor.monitor.report_schema_findings"), \
                     patch("src.data_platform.log_sink.install"), \
                     patch("src.data_platform.log_sink.chain_sigterm"), \
                     patch("src.strategy_framework.broker.get_interface_row",
                           return_value={"provider": "xtp", "market": "astock",
                                         "credentials": {}, "params": {}}), \
                     patch("src.strategy_framework.md_gateway.create_md_gateway",
                           return_value=_GW()), \
                     patch("src.providers.xtp.session.is_trading_day",
                           return_value=True), \
                     patch.object(EngineLoop, "run", _fake_run), \
                     patch.object(M, "exit_process"):
                    M.main()
            finally:
                if old is None:
                    os.environ.pop("ACCOUNT_ID", None)
                else:
                    os.environ["ACCOUNT_ID"] = old

            sm = {h.name: h for h in captured["loop"]._hooks}["subs-poll"].fn.__self__
            assert isinstance(sm, SubscriptionManager)
            want = sm._desired()
            assert "689997.SHSE" in want, "持仓 .SSE 必须归一 .SHSE 进订阅集"
            assert "689996.SHSE" not in want and "689996.SHSE" not in want, \
                "他账号持仓标的不得到本 hub 订阅（account_id 过滤失效=红）"
        finally:
            with get_conn() as c:
                c.execute("DELETE FROM position_snapshot WHERE account_id = ANY(%s)",
                          ([acc_a, acc_b],))
                c.execute("DELETE FROM trading_account WHERE id = ANY(%s)",
                          ([acc_a, acc_b],))
                c.commit()


# ─────────────────────────── S8 配置（缺省/覆写/值域） ───────────────────────────

class _EmptyConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return self

    def fetchall(self):
        return []


def _fake_rules(policy=None, tick_size="0.001"):
    """P2b：adapter 规则面的测试替身（规格层不读库）。"""
    from src.data_platform.rules import RuleSet, _DummyAcc
    pol = policy or _policy()
    ins = SimpleNamespace(tick_size=tick_size, price_band=None, lot_size=1, order_types=[],
                          sellable_rule="volume_minus_frozen", venue_flags={},
                          position_side="long_only", t_plus=1, order_range=None, symbol="")
    acc = _DummyAcc({"opponent_depth": pol["opponent_depth"],
                     "aggression": pol["aggression"],
                     "max_order_qty": pol["max_order_qty"]})
    return RuleSet(ms=None, ins=ins, acc=acc, state="fresh")


def _patch_rules(policy=None, tick_size="0.001"):
    return patch.object(ExecutionAdapter, "_ruleset",
                        lambda self, sym="": _fake_rules(policy, tick_size))


class TestS8AccountClosePolicy:
    """P2b：策略源=账户 params；缺省按 provider；越界拒写；旧 system_config 键必须退役。"""

    def test_missing_params_fall_back_to_provider_defaults(self):
        a = _Adapter([])
        a.account_id = None            # 无账户上下文 → provider 缺省
        pol = a.close_policy()
        assert pol["aggression"] == 0.10 and pol["max_order_qty"] == 100000
        assert pol["opponent_depth"] == 1

    def test_system_config_close_policy_retired(self):
        from src.web_api.routes.system import SYSTEM_CONFIG_BOUNDS
        assert not [k for k in SYSTEM_CONFIG_BOUNDS if k.startswith("close_policy.")]
        if _db_up():
            from src.data_platform.db import get_conn
            with get_conn() as c:
                n = c.execute("SELECT count(*) FROM system_config WHERE key LIKE 'close_policy.%'").fetchone()[0]
            assert n == 0, "旧配置键必须退役（0177 contract）"

    def test_out_of_bounds_rejected_by_specs(self):
        from src.data_platform import specs
        with pytest.raises(ValueError):
            specs.update_close_policy(0, {"opponent_depth": 9})
        with pytest.raises(ValueError):
            specs.update_close_policy(0, {"aggression": 0.9})

    @pytest.mark.skipif(not _db_up(), reason="真库不可用")
    def test_account_params_override_effective(self):
        from src.data_platform import specs
        from src.data_platform.db import get_conn
        with get_conn() as c:
            row = c.execute("SELECT id, params FROM trading_account ORDER BY id LIMIT 1").fetchone()
        acc_id, prev = row[0], row[1]
        try:
            specs.update_close_policy(acc_id, {"opponent_depth": 2, "aggression": 0.25,
                                               "max_order_qty": 500})
            a = _Adapter([])
            a.account_id = acc_id
            assert a.close_policy()["aggression"] == 0.25
            assert a.max_close_order_qty("600000.SHSE") == 500
        finally:
            with get_conn() as c:
                c.execute("UPDATE trading_account SET params=%s::jsonb WHERE id=%s",
                          (json.dumps(prev or {}, ensure_ascii=False), acc_id))
                c.commit()
            specs.invalidate()
