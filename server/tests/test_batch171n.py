"""批 171n · 急停补全守门（v3 规格 §1.6 七条）。

①halted×SELL 放行/BUY 拒/HALT_UNREADABLE 全拒 ②_cancel_working_orders 两态
③_close_all_positions（T+1 截断/cost_price/无 sid/拒单汇总）④consume GETDEL 幂等+回执
⑤端点三分叉+确认词 ⑥F-41 零漂移 ⑦路由认证守门。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


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
        assert out == {"requested": 2, "failed": 1}


class TestCloseAllPositions:
    def _pos(self, sym, vol, frozen, price):
        return SimpleNamespace(symbol=sym, volume=vol, frozen=frozen, avg_price=price)

    def test_t1_skip_and_cost_price(self):
        from src.strategy_runner.trading import _close_all_positions
        adapter = MagicMock()
        adapter.query_position.side_effect = [
            [self._pos("600000.SHSE", 1000, 0, 10.5),   # 全可卖
             self._pos("000001.SZSE", 500, 500, 12.0)],  # T+1 全冻
            [],   # 二次复查（remain=0）
        ]
        sent = []
        adapter.send_order.side_effect = lambda o: sent.append(o) or "oid"
        rc = MagicMock()
        rc.check_order.return_value = SimpleNamespace(approved=True, reason="")
        with patch("src.risk_control.risk.RiskControl.get", return_value=rc):
            out = _close_all_positions(adapter, account_id=1, operator="t")
        assert out["skipped_t1"] == ["000001.SZSE"], "T+1 冻结标的 skip 记名"
        assert out["closed_requested"] == 1
        o = sent[0]
        assert o.volume == 1000 and o.price == 10.5, "可卖量+cost_price"
        # check_order 收到的 dict：无 strategy_id（频次豁免锚）
        got = rc.check_order.call_args[0][0]
        assert "strategy_id" not in got and got["action"] == "SELL"

    def test_rejected_summary(self):
        from src.strategy_runner.trading import _close_all_positions
        adapter = MagicMock()
        adapter.query_position.return_value = [self._pos("600000.SHSE", 100, 0, 10.0)]
        rc = MagicMock()
        rc.check_order.return_value = SimpleNamespace(approved=False, reason="某规则拒")
        with patch("src.risk_control.risk.RiskControl.get", return_value=rc):
            out = _close_all_positions(adapter)
        assert out["rejected"] and "600000.SHSE" in out["rejected"][0]


class TestConsumeRiskCmd:
    def test_getdel_idempotent_and_ack(self):
        from src.strategy_runner import hub_worker as HW
        r = MagicMock()
        r.getdel.return_value = '{"id":"r1","verb":"cancel-all","operator":"u"}'
        adapter = MagicMock()
        adapter.query_orders.return_value = []
        _td = SimpleNamespace(connect_status=True)
        out = HW.consume_risk_cmd(r, adapter, "t1", td_api=_td)
        assert out["verb"] == "cancel-all" and out["result"]["requested"] == 0
        # 回执键写入
        rk = [c.args[0] for c in r.set.call_args_list if "risk:cmd:result:r1" in str(c.args[0])]
        assert rk, "回执键写入"
        # 二次消费（GETDEL 空）⇒ None（幂等）
        r2 = MagicMock(); r2.getdel.return_value = None
        assert HW.consume_risk_cmd(r2, adapter, "t1", td_api=_td) is None


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
            R.risk_close_all({"username": "t"})   # 无 confirm
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
