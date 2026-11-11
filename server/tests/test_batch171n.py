"""批 171n · 急停补全守门（v3 规格 §1.6 七条）。

①halted×SELL 放行/BUY 拒/HALT_UNREADABLE 全拒 ②_cancel_working_orders 两态
③_close_all_positions（T+1 截断/cost_price/无 sid/拒单汇总）④consume GETDEL 幂等+回执
⑤端点三分叉+确认词 ⑥F-41 零漂移 ⑦路由认证守门。
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _open_stub(sym, volume, price, account_id, operator, req_id, seq, action, cid=None):
    """`_close_order_open` 替身（不触库）：返回 (None, cid)——cid 形态与真实实现一致。"""
    return None, cid or f"emc-{req_id}-{sym}-{seq}"


def _rc(halted=True, unreadable=False):
    """构造 RiskControl（Valkey mock——halted 态/故障态）。"""
    from src.risk_control.risk import RiskControl
    rc = RiskControl.__new__(RiskControl)
    r = MagicMock()
    if unreadable:
        r.get.side_effect = RuntimeError("valkey down")
    else:
        r.get.side_effect = lambda k, **kw: ("1" if k == "risk:halted" else None)
    rc._redis = r
    rc._rules_loaded_at = 0
    rc._rules = None
    return rc


class TestHaltedSellPass:
    """件一：halted 分支方向判（A-P0-1 判据=action）。"""

    def test_halted_sell_passes_gate1(self):
        rc = _rc(halted=True)
        d = rc._check_order_inner.__wrapped__(rc, {
            "symbol": "600000.SHSE", "action": "SELL", "volume": 100, "price": 10.0,
        }) if hasattr(rc._check_order_inner, "__wrapped__") else None
        # 直测分支语义（全链测试见④）： halted+SELL 不得在第一步被 HALTED 拒
        with patch.object(rc, "_check_order_inner", side_effect=lambda *a, **k: _inner_probe(a[0] if a else {})):
            pass
        # 简化直测：走 check_order 带 mock 后链
        def _inner(order, account_id=None, *, paper=False):
            # 第一步 halted 分支内联复刻（真源在 risk.py——这里跑真的 _check_order_inner
            # 并断言其未因 HALTED 返回）：直接调真函数
            return None
        # 真实直调： halted+SELL ⇒ 非 HALTED 拒（可能因 LIVE_SWITCH_OFF 拒=通过第 1 步）
        with patch.object(rc, "is_halted", return_value=True), \
             patch.object(rc, "halt_reason", return_value="test"):
            d = rc._check_order_inner({"symbol": "600000.SHSE", "action": "SELL",
                                       "volume": 100, "price": 10.0})
        assert d.rule != "HALTED", f"平仓方向被熔断拒: {d.rule} {d.reason}"
        assert d.rule == "LIVE_SWITCH_OFF", "过了第 1 步撞实盘开关（未开）=预期路径"

    def test_halted_buy_rejected(self):
        rc = _rc(halted=True)
        with patch.object(rc, "is_halted", return_value=True), \
             patch.object(rc, "halt_reason", return_value="test"):
            d = rc._check_order_inner({"symbol": "600000.SHSE", "action": "BUY",
                                       "volume": 100, "price": 10.0})
        assert d.rule == "HALTED" and not d.approved

    def test_unreadable_rejects_all(self):
        rc = _rc(unreadable=True)
        d = rc._check_order_inner({"symbol": "600000.SHSE", "action": "SELL",
                                   "volume": 100, "price": 10.0})
        assert d.rule == "HALT_UNREADABLE" and not d.approved


class TestMaxSingleAmountSellExempt:
    def test_sell_exempt_from_truncation(self):
        from src.risk_control.risk import RiskControl
        rc = RiskControl.__new__(RiskControl)
        r = MagicMock(); r.get.return_value = None
        rc._redis = r; rc._rules_loaded_at = 0; rc._rules = None
        with patch.object(rc, "_maybe_reload_rules"), \
             patch.object(rc, "is_halted", return_value=False), \
             patch.object(rc, "_check_order_inner",
                          side_effect=lambda o, a=None, **k: _etf_conv(o)):
            pass
        # 直测 _check_etf_conv 的 SELL 豁免（绕外链只测该函数）
        d = _etf_conv({"symbol": "600000.SHSE", "action": "SELL",
                       "volume": 100000, "price": 100.0})
        assert d.approved and "豁免" in d.reason


def _etf_conv(order):
    from src.risk_control.risk import RiskDecision
    # 复刻调用点（真函数依赖 self._rules——最小实例化）
    from src.risk_control.risk import RiskControl
    rc = RiskControl.__new__(RiskControl)
    rc._rules = {"etf_conv": {"max_single_amount": 100000}}
    return rc._check_etf_conv(order, None)


class TestCancelWorkingOrders:
    def test_two_states(self):
        from src.strategy_runner.trading import _cancel_working_orders
        from vnpy.trader.constant import Status
        adapter = MagicMock()
        adapter.query_orders.return_value = [
            SimpleNamespace(status=Status.NOTTRADED, vt_orderid="a"),
            SimpleNamespace(status=Status.NOTTRADED, vt_orderid="b"),
            SimpleNamespace(status=Status.ALLTRADED, vt_orderid="c"),   # 非在场
        ]
        adapter.cancel_order.side_effect = [None, RuntimeError("x")]
        out = _cancel_working_orders(adapter)
        assert out["requested"] == 2 and out["failed"] == 1 and out["untranslatable"] == 0


class TestCloseAllPositions:
    """批 171s：_close_all_positions 新契约（同源 tick + Adapter 定价 + 派生方向）。

    旧断言意图原样保留（T+1 记名 / 风控拒单进回执），只随新形态改写：
    - `_quote_price` 返回 (tick, reason) 元组；
    - 定价由 `adapter.close_reference_price(tick,pos,action)` 给出（不再是成本价/腾讯）；
    - `_close_order_open` 返回 (order_log.id, cid)。
    """

    def _pos(self, sym, vol, frozen, price, direction="direction_long"):
        return SimpleNamespace(symbol=sym, volume=vol, frozen=frozen,
                               avg_price=price, direction=direction)

    def _adapter(self, positions, px):
        a = MagicMock()
        a.query_position.side_effect = [list(positions), []]
        a.query_orders.return_value = []
        a.cancel_order.return_value = None
        a.close_action.side_effect = (
            lambda p: "BUY" if getattr(p, "direction", "") == "direction_short" else "SELL")
        a.close_reference_price.side_effect = lambda tick, pos, action: float(tick["px"])
        a.round_close_price.side_effect = lambda p, tick: p
        a.max_close_order_qty.return_value = 100000
        a.get_vt_orderid.return_value = None
        a.send_order.side_effect = lambda o: f"c{len(a.send_order.call_args_list)}"
        return a

    def test_t1_skip_and_cost_price(self):
        from src.strategy_runner import trading as T
        adapter = self._adapter(
            [self._pos("600000.SHSE", 1000, 0, 10.5),     # 全可卖（成本价 10.5）
             self._pos("000001.SZSE", 500, 500, 12.0)],   # T+1 全冻
            9.87)
        rc = MagicMock()
        rc.check_order.return_value = SimpleNamespace(approved=True, reason="")
        with patch("src.risk_control.risk.RiskControl.get", return_value=rc), \
             patch.object(T, "_quote_price", return_value=({"px": 9.87}, "")), \
             patch.object(T, "_close_order_open", side_effect=_open_stub), \
             patch.object(T, "write_cmd_result"), \
             patch("time.sleep"):
            out = T._close_all_positions(adapter, account_id=1, operator="t")
        assert out["skipped_t1"] == ["000001.SZSE"], "T+1 冻结标的 skip 记名"
        assert out["closed_requested"] == 1
        o = adapter.send_order.call_args_list[0].args[0]
        # 价来自同源 tick 的参考价（9.87），不是成本价 10.5，也不打折
        assert o.volume == 1000 and abs(o.price - 9.87) < 1e-6, \
            "价格必须取同源 tick 参考价（成本价 10.5 接入=红）"
        # check_order 收到的 dict：无 strategy_id（频次豁免锚），方向为派生 SELL
        got = rc.check_order.call_args[0][0]
        assert "strategy_id" not in got and got["action"] == "SELL"
        assert got["reason"] == "emergency_close_all"

    def test_rejected_summary(self):
        from src.strategy_runner import trading as T
        adapter = self._adapter([self._pos("600000.SHSE", 100, 0, 10.0)], 10.0)
        rc = MagicMock()
        rc.check_order.return_value = SimpleNamespace(approved=False, reason="某规则拒")
        with patch("src.risk_control.risk.RiskControl.get", return_value=rc), \
             patch.object(T, "_quote_price", return_value=({"px": 10.0}, "")), \
             patch.object(T, "_close_order_open", side_effect=_open_stub), \
             patch.object(T, "write_cmd_result"), \
             patch("time.sleep"):
            out = T._close_all_positions(adapter)
        assert out["rejected"] and "600000.SHSE" in out["rejected"][0]
        adapter.send_order.assert_not_called()


class TestConsumeRiskCmd:
    def test_getdel_idempotent_and_ack(self):
        """批 171s 件四：主循环拍 dispatch-only——GETDEL→put_nowait→立即返回。

        不断言 `result`/`r.set`（执行已搬入 EmergencyWorker 专用线程，回执由它写）。
        """
        import queue as _queue
        from src.strategy_runner import hub_worker as HW
        r = MagicMock()
        r.getdel.return_value = '{"id":"r1","verb":"cancel-all","operator":"u","operator_uid":7}'
        adapter = MagicMock()
        q = _queue.Queue()
        with patch("src.strategy_runner.hub_worker.time.sleep") as sl:
            out = HW.consume_risk_cmd(r, adapter, "t1",
                                      td_api=SimpleNamespace(connect_status=True), cmd_queue=q)
        assert out == {"id": "r1", "verb": "cancel-all", "dispatched": True}
        item = q.get_nowait()
        assert item["req_id"] == "r1" and item["verb"] == "cancel-all"
        assert item["operator_uid"] == 7, "operator_uid 必须一并投递（SSE 定向前提）"
        # 主循环拍：不写回执、不碰 adapter、不 sleep
        r.set.assert_not_called()
        adapter.query_orders.assert_not_called()
        sl.assert_not_called()
        # 二次消费（GETDEL 空）⇒ None（幂等）
        r2 = MagicMock(); r2.getdel.return_value = None
        assert HW.consume_risk_cmd(r2, adapter, "t1", cmd_queue=q) is None


class TestEndpoints:
    def _client(self):
        from fastapi import FastAPI
        from src.web_api.routes.risk import router
        from src.web_api.auth import get_current_user
        app = FastAPI()
        app.include_router(router)
        return app

    def test_no_runner_409(self):
        from src.web_api.errors import ApiError
        from src.web_api.routes import risk as R
        with patch.object(R, "_live_runners", return_value=[]):
            with pytest.raises(ApiError) as ei:
                R._dispatch_risk_cmd("cancel-all", {"username": "t"})
            assert ei.value.status_code == 409

    def test_dispatched_and_ack(self):
        import json
        from src.web_api.routes import risk as R
        r = MagicMock()
        r.set.return_value = True
        # 立即回执
        r.get.side_effect = lambda k, **kw: (
            json.dumps({"tid": "t1", "verb": "cancel-all",
                        "result": {"requested": 3, "failed": 0}})
            if "risk:cmd:result:" in k else None)
        with patch.object(R, "_live_runners", return_value=[("t1", 1)]), \
             patch("src.quant_common.redis_client.business_redis", return_value=r):
            out = R._dispatch_risk_cmd("cancel-all", {"username": "t"})
        assert out["status"] == "done" and out["results"]["t1"]["result"]["requested"] == 3

    def test_close_all_confirm_gate(self):
        from src.web_api.errors import ApiError
        from src.web_api.routes import risk as R
        with pytest.raises(ApiError) as ei:
            R.risk_close_all({}, {"username": "t"})   # 无 confirm（body 空）
        assert ei.value.status_code == 400

class TestHeartbeatDecoding:
    """2026-11-09 e2e 抓到的真因：business_redis 默认 bytes 键 ⇒ hb.get("ts") 恒 None
    ⇒ cancel-all/close-all/halt 恒 409（mock 用 str-key dict 全绿掩盖）。"""

    def test_live_runners_accepts_bytes_keys(self, monkeypatch):
        import time as _t
        from src.web_api.routes import risk as R
        r = MagicMock()
        r.hgetall.return_value = {b"ts": str(_t.time()).encode(), b"working_orders": b"3"}
        monkeypatch.setattr("src.data_platform.config_store.live_running_tasks",
                            lambda: [(11, 1)])
        with patch("src.quant_common.redis_client.business_redis", return_value=r):
            assert R._live_runners() == [("11", 1)]

    def test_exposure_running_requires_fresh_hb(self, monkeypatch):
        import time as _t
        from src.web_api.routes import risk as R
        monkeypatch.setattr("src.data_platform.config_store.trading_accounts_with_flags",
                            lambda: [(1, "a", "xtp", True, False)])
        monkeypatch.setattr("src.data_platform.config_store.exposure_snapshots", lambda: {})
        monkeypatch.setattr("src.data_platform.config_store.live_running_tasks",
                            lambda: [("11", 1)])

        def _run(hb):
            r = MagicMock(); r.hgetall.return_value = hb
            with patch("src.quant_common.redis_client.business_redis", return_value=r), \
                 patch("src.risk_control.risk.RiskControl.get") as rc:
                rc.return_value.halt_ts.return_value = None
                return R.risk_exposure(_p={"username": "t"})["items"][0]

        fresh = _run({b"ts": str(_t.time()).encode(), b"working_orders": b"3"})
        assert fresh["running"] is True and fresh["working_orders"] == 3

        stale = _run({b"ts": str(_t.time() - 120).encode(), b"working_orders": b"3"})
        assert stale["running"] is False and stale["working_orders"] is None

    def test_halt_succeeds_when_dispatch_fails(self):
        """halt=全局止血：撤单下发失败（无 runner 409）不得让 halt 请求失败。"""
        from src.web_api.errors import ApiError
        from src.web_api.routes import risk as R
        with patch("src.risk_control.risk.RiskControl.get") as rc, \
             patch.object(R, "_dispatch_risk_cmd",
                          side_effect=ApiError(409, "RISK_NO_ACTIVE_RUNNER", "x")), \
             patch.object(R, "audit_log"):
            out = R.risk_halt({"username": "t"})
        assert out["halted"] is True
        assert out["cancel"]["error"] == "RISK_NO_ACTIVE_RUNNER"
        rc.return_value.emergency_halt.assert_called_once()


class TestThinTdGatewayErrorCallback:
    """vnpy_xtp 在撤单/委托失败时调 gateway.write_error——BaseGateway 没有此方法，
    ThinTdGateway 必须补（否则撤单失败回调抛 AttributeError，熔断沿撤单路径被打断）。"""

    def test_write_error_exists_and_never_raises(self, monkeypatch):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import src.strategy_framework.md_session as M
        import src.strategy_runner.td_registry as TD
        # 绑定点在 td_registry 模块命名空间（模块级 import）——patch broker 会因导入顺序失效
        monkeypatch.setattr(TD, "build_xtp_setting",
                            lambda **kw: {"账号": "x", "密码": "y", "客户号": 1,
                                          "交易地址": "h", "交易端口": 1, "授权码": "z"})
        monkeypatch.setattr(M, "load_xtp_window_cfg", lambda: (10, 10))
        monkeypatch.setattr(M, "xtp_session_window_open", lambda *a, **k: False)
        monkeypatch.setattr(M, "is_trading_day", lambda *a, **k: True)
        from vnpy.event import EventEngine
        from src.strategy_runner.td_registry import build_td_runtime
        rt = build_td_runtime("xtp", EventEngine(), 999, 1, 123)
        gw = rt["gw"]
        assert hasattr(gw, "write_error")
        gw.write_error("撤单失败", {"error_id": 1, "error_msg": "mock"})   # 不抛即过
        gw.write_error("撤单失败", None)

class TestHeldVolumeSymbolAliases:
    """2026-11-09 e2e 抓真因：XTP 持仓 symbol 用 SSE，任务标的用 SHSE ⇒ 可卖仓恒 0
    ⇒ 所有 SELL 被「截断到可卖仓 0」丢弃（止损失效）。"""

    def test_candidates_cover_shse_and_sse(self):
        from src.strategy_framework.strategy import position_symbol_candidates
        assert set(position_symbol_candidates("600000.SHSE")) == {"600000.SHSE", "600000.SSE"}
        assert set(position_symbol_candidates("600000.SSE")) == {"600000.SSE", "600000.SHSE"}
        assert position_symbol_candidates("000001.SZSE") == ["000001.ZSE"] if False else \
            position_symbol_candidates("000001.SZSE") == ["000001.SZSE"]

    def test_held_volume_finds_sse_row(self):
        from src.data_platform.db import get_conn
        from src.strategy_framework.strategy import Strategy
        try:
            with get_conn() as c:   # 临时账号（dev 库无 prod 账号——FK 需要）
                c.execute("INSERT INTO trading_account (id,name,provider,market,capabilities) "
                          "VALUES (990001,'e2e-gate','xtp','astock','{}')")
                c.execute("INSERT INTO position_snapshot (account_id, symbol, direction, volume, frozen) "
                          "VALUES (990001, '999999.SSE', 'direction_net', 100, 30)")
                c.commit()
            s = Strategy.__new__(Strategy)
            s.symbol = "999999.SHSE"
            s.account_id = 990001
            assert s._held_volume() == 70, "SSE 行必须被 SHSE 标的匹配到"
        finally:
            with get_conn() as c:
                c.execute("DELETE FROM position_snapshot WHERE account_id=990001")
                c.execute("DELETE FROM trading_account WHERE id=990001")
                c.commit()

class TestBodyWiring:
    """2026-11-09 e2e 抓真因：confirm/accounts 从 auth payload 取 ⇒ 恒 400 / 恒全量。
    必须从请求 Body 取（前端 body 里的 CONFIRM/accounts 才生效）。"""

    def test_close_all_confirm_from_body(self):
        from src.web_api.routes import risk as R
        with patch("src.risk_control.risk.RiskControl.get") as rc, \
             patch.object(R, "_dispatch_risk_cmd", return_value={"status": "done"}) as d:
            rc.return_value.is_halted.return_value = True
            assert R.risk_close_all({"confirm": "CONFIRM", "accounts": [7]},
                                    {"username": "t"})["status"] == "done"
            assert d.call_args.kwargs["accounts"] == [7]

    def test_cancel_all_accounts_from_body(self):
        from src.web_api.routes import risk as R
        with patch.object(R, "_dispatch_risk_cmd", return_value={"status": "done"}) as d:
            R.risk_cancel_all({"accounts": [3]}, {"username": "t"})
            assert d.call_args.kwargs["accounts"] == [3]

class TestCloseAllChunkingAndExchangeReject:
    """2026-11-09 e2e 实证：成本价越界被拒 + 超百万股单笔被拒（171s 形态保留原意图）。"""

    def _pos(self, sym, vol, frozen=0, price=1.0):
        return SimpleNamespace(symbol=sym, volume=vol, frozen=frozen,
                               avg_price=price, direction="direction_long")

    def _adapter(self, positions, px, max_qty=100000):
        a = MagicMock()
        a.query_position.side_effect = [list(positions), []]
        a.query_orders.return_value = []
        a.cancel_order.return_value = None
        a.close_action.return_value = "SELL"
        a.close_reference_price.side_effect = lambda tick, pos, action: px
        a.round_close_price.side_effect = lambda p, tick: p
        a.max_close_order_qty.return_value = max_qty
        a.get_vt_orderid.return_value = None
        a.send_order.side_effect = lambda o: f"c{len(a.send_order.call_args_list)}"
        return a

    def test_chunked_by_max_order_qty(self):
        from src.strategy_runner import trading as T
        a = self._adapter([self._pos("511010.SSE", 2_500_000)], 1.0)
        rc = MagicMock(); rc.check_order.return_value = SimpleNamespace(approved=True, reason="")
        with patch("src.risk_control.risk.RiskControl.get", return_value=rc), \
             patch.object(T, "_quote_price", return_value=({"px": 1.0}, "")), \
             patch.object(T, "_close_order_open", side_effect=_open_stub), \
             patch.object(T, "write_cmd_result"), \
             patch("time.sleep"):
            out = T._close_all_positions(a)
        sent = [c.args[0] for c in a.send_order.call_args_list]
        assert sum(o.volume for o in sent) == 2_500_000, "总量守恒"
        assert all(o.volume <= 100_000 for o in sent), "每单不超上限（10 万股）"
        assert out["closed_requested"] == len(sent) >= 25

    def test_exchange_reject_counted(self):
        from vnpy.trader.constant import Status
        from src.strategy_runner import trading as T
        a = self._adapter([self._pos("600000.SSE", 100)], 10.0)
        a.get_vt_orderid.return_value = "oid-x"
        rejected = SimpleNamespace(vt_orderid="oid-x", symbol="600000.SSE",
                                   status=Status.REJECTED)
        a.query_orders.side_effect = [[], [rejected]]   # 撤单轮=空；拒单回报轮=REJECTED
        rc = MagicMock(); rc.check_order.return_value = SimpleNamespace(approved=True, reason="")
        with patch("src.risk_control.risk.RiskControl.get", return_value=rc), \
             patch.object(T, "_quote_price", return_value=({"px": 10.0}, "")), \
             patch.object(T, "_close_order_open", side_effect=_open_stub), \
             patch.object(T, "write_cmd_result"), \
             patch("time.sleep"):
            out = T._close_all_positions(a)
        assert any("exchange-rejected" in r for r in out["rejected"]), "交易所拒单必须进回执"


class TestCloseAllAuditAndQuote:
    def test_quote_price_prefers_realtime(self):
        """同源取价：只走 get_hub_tick；腾讯外部快照出口退役（命中即炸）。"""
        from src.strategy_runner import trading as T
        tick = {"last": 12.3, "bid": [12.2], "ask": [12.4]}
        with patch("src.data_platform.hub_quote.get_hub_tick", return_value=tick) as gh, \
             patch("src.data_platform.market_snapshot.get_quote",
                   side_effect=AssertionError("腾讯快照出口必须退役")) as gq:
            t, reason = T._quote_price("600000.SSE", 1)
        assert (t, reason) == (tick, ""), "可用 tick 原样返回且 reason 为空"
        gh.assert_called_once_with(1, "600000.SSE")
        gq.assert_not_called()

    def test_audit_open_and_mark(self):
        from src.data_platform.db import get_conn
        from src.strategy_runner import trading as T
        req = "ut" + uuid.uuid4().hex[:8]
        oid, cid = T._close_order_open("999999.SHSE", 100, 10.0, None, "t", req, 1, "SELL")
        try:
            assert oid, "急停单必须落 order_log（审计缺口）"
            assert cid == f"emc-{req}-999999.SHSE-1", "预生成 cid 形态"
            with get_conn() as c:
                row = c.execute(
                    "SELECT symbol, action, volume, status, client_order_id "
                    "FROM order_log WHERE id=%s", (oid,)).fetchone()
            assert row[0] == "999999.SHSE" and row[1] == "SELL" and row[3] == "submitting"
            assert row[4] == cid, "INSERT 在 send 前就写 client_order_id"
            adapter = MagicMock()
            adapter.get_vt_orderid.return_value = "XTP.1"
            T._close_order_mark(oid, adapter, cid, "submitted")
            with get_conn() as c:
                row = c.execute("SELECT status, vt_orderid FROM order_log WHERE id=%s",
                                (oid,)).fetchone()
            assert row[0] == "submitted" and row[1] == "XTP.1"
        finally:
            with get_conn() as c:
                c.execute("DELETE FROM order_log WHERE id=%s", (oid,))
                c.commit()

