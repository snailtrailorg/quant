"""批 171o · 紧急处置页守门（v3 规格 §1.4）。

后端：exposure 聚合/accounts 过滤+去重/串联序/halt_ts。FE 纯函数三件（utils）。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.web_api.routes import risk as R


class TestExposure:
    def _run(self, monkeypatch, accts, snaps, live, hb_map):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = accts
        # 171q：真源含 is_virtual 标志（(id, name, provider, enabled, is_virtual)）
        monkeypatch.setattr("src.data_platform.config_store.trading_accounts_with_flags",
                            lambda: [(a[0], a[1], a[2], True, False) for a in accts])
        monkeypatch.setattr("src.data_platform.config_store.exposure_snapshots", lambda: snaps)
        monkeypatch.setattr("src.data_platform.config_store.live_running_tasks", lambda: live)
        r = MagicMock()
        r.hgetall.side_effect = lambda k: hb_map.get(k, {})
        with patch("src.quant_common.redis_client.business_redis", return_value=r), \
             patch("src.risk_control.risk.RiskControl.get") as rc:
            rc.return_value.halt_ts.return_value = None
            return R.risk_exposure(_p={"username": "t"})

    def test_stale_unknown_and_working(self, monkeypatch):
        """stale>120s ⇒ 市值 None（未知非 0）；working_orders=-1（断线）与数值分态。"""
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc)
        fresh = now - dt.timedelta(seconds=30)
        stale = now - dt.timedelta(seconds=200)
        accts = [(4, "XTP测试", "xtp"), (51, "PC-guard", "xtp")]
        snaps = {4: {"pos_ts": fresh, "pos_value": 12340.0, "snap": (100000.0, -1200.0, fresh)},
                 51: {"pos_ts": stale, "pos_value": 0.0, "snap": None}}
        live = [(101, 4)]
        hb = {"quant:hb:task:101": {"ts": "9999999999", "working_orders": "5"}}
        out = self._run(monkeypatch, accts, snaps, live, hb)
        a4 = next(i for i in out["items"] if i["account_id"] == 4)
        a51 = next(i for i in out["items"] if i["account_id"] == 51)
        assert a4["working_orders"] == 5 and a4["position_value"] == 12340.0
        assert a51["position_value"] is None and a51["stale"] is True, "stale=未知非 0"
        assert a51["running"] is False


class TestDispatchAccounts:
    def test_filter_and_dedup(self, monkeypatch):
        """accounts 过滤（集外不收）+ 同 account 双 runner 去重（只发一 tid）。"""
        import json
        r = MagicMock()
        r.get.side_effect = lambda k, **kw: (
            json.dumps({"tid": "t1", "verb": "cancel-all", "result": {"requested": 1, "failed": 0}})
            if "risk:cmd:result:" in k else None)
        # 走真 _live_runners（含 accounts 过滤）——patch 其依赖层；心跳键预置未来 ts 保新鲜
        r.hgetall.side_effect = lambda k: {"ts": "9999999999"}
        with patch("src.quant_common.redis_client.business_redis", return_value=r), \
             patch("src.data_platform.config_store.live_running_tasks",
                   return_value=[("t1", 4), ("t2", 4), ("t3", 51)]):
            out = R._dispatch_risk_cmd("cancel-all", {"username": "t"}, accounts=[4])
        # 只发 t1（account 4 去重）；t3（51）被过滤
        set_keys = [c.args[0] for c in r.set.call_args_list if str(c.args[0]).startswith("risk:cmd:")]
        assert set_keys == ["risk:cmd:t1"], f"去重+过滤: {set_keys}"
        assert out["status"] == "done"

    def test_no_match_account_400(self):
        from src.web_api.errors import ApiError
        with patch.object(R, "_live_runners", return_value=[]), \
             patch("src.data_platform.config_store.live_running_tasks", return_value=[("t1", 4)]):
            with pytest.raises(ApiError) as ei:
                R._dispatch_risk_cmd("cancel-all", {"username": "t"}, accounts=[51])
            assert ei.value.status_code == 400

    def test_invalid_account_400(self):
        from src.web_api.errors import ApiError
        with patch("src.data_platform.config_store.live_running_tasks", return_value=[("t1", 4)]):
            with pytest.raises(ApiError) as ei:
                R._dispatch_risk_cmd("cancel-all", {"username": "t"}, accounts=[999])
            assert ei.value.status_code == 400


class TestHaltThenCancelOrder:
    def test_halt_before_dispatch(self):
        """P0-1：halt 端点先置 halted 再 dispatch（顺序铁律）。"""
        calls = []
        with patch("src.risk_control.risk.RiskControl.get") as rc, \
             patch.object(R, "_dispatch_risk_cmd", side_effect=lambda *a, **k: calls.append("dispatch") or {"status": "done"}):
            rc.return_value.emergency_halt.side_effect = lambda *a: calls.append("halt")
            R.risk_halt({"username": "t"})
        assert calls == ["halt", "dispatch"], "先停后撤（零窗口）"

    def test_close_all_preamble_halt(self):
        """close-all 未熔断先置 halted（全包含语义）。"""
        with patch("src.risk_control.risk.RiskControl.get") as rc, \
             patch.object(R, "_dispatch_risk_cmd", return_value={"status": "done"}) as d:
            rc.return_value.is_halted.return_value = False
            R.risk_close_all({"confirm": "CONFIRM"}, {"username": "t"})
            rc.return_value.emergency_halt.assert_called_once()
            d.assert_called_once()

    def test_close_all_confirm_gate(self):
        from src.web_api.errors import ApiError
        with pytest.raises(ApiError):
            R.risk_close_all({}, {"username": "t"})


class TestConsumeCloseAllChain:
    """批 171s：执行链已搬入 EmergencyWorker 专用线程（consume_risk_cmd 只投递）。

    此处直接驱动 worker 的 _handle（同步执行路径），断言串联序 + TD 断线防线 +
    终局 uid 定向零内容 SSE 帧仍成立。
    """

    def _worker(self, adapter, td_api):
        import threading
        from src.strategy_runner import hub_worker as HW
        return HW.EmergencyWorker(adapter=adapter, td_api=td_api, account_id=1,
                                  tid="t1", closing=threading.Event())

    def test_chain_and_disconnect_guard(self):
        from src.strategy_runner import trading as T
        w = self._worker(MagicMock(), SimpleNamespace(connect_status=True))
        with patch.object(T, "_cancel_working_orders", return_value={"requested": 3, "failed": 0}) as c, \
             patch.object(T, "_close_all_positions", return_value={"closed_requested": 2}) as p, \
             patch.object(T, "write_cmd_result") as wr, \
             patch("src.data_platform.hub_quote.get_retry_config",
                   return_value={"deadline_s": 120}), \
             patch("src.strategy_runner.hub_worker.time.sleep"), \
             patch("src.quant_common.eventbus.bus.publish_cross_process") as pub:
            w._handle({"req_id": "r1", "verb": "close-all", "operator": "u",
                       "operator_uid": 7})
        c.assert_called_once()
        p.assert_called_once()
        assert p.call_args.kwargs["req_id"] == "r1"
        assert not w._closing.is_set(), "整轮结束必须清 Gate"
        # 终局 :final 写入 + uid 定向零内容 SSE 帧
        assert any(call.kwargs.get("final") is True for call in wr.call_args_list)
        pub.assert_called_once_with(7, "emergency_close_final", {})

    def test_td_disconnected_refuses(self):
        import json
        from src.strategy_runner import trading as T
        w = self._worker(MagicMock(), SimpleNamespace(connect_status=False))
        with patch.object(T, "_close_all_positions") as p, \
             patch.object(T, "write_cmd_result") as wr, \
             patch("src.quant_common.eventbus.bus.publish_cross_process"):
            w._handle({"req_id": "r2", "verb": "close-all"})
        p.assert_not_called()
        w._adapter.query_orders.assert_not_called()
        writes = [json.dumps(call.args[1], ensure_ascii=False)
                  for call in wr.call_args_list if len(call.args) > 1]
        assert any("td_disconnected" in blob for blob in writes), \
            "td_disconnected 必须写进回执/终局（可见非静默）"


class TestHaltTsKeys:
    def test_three_key_lifecycle(self):
        from src.risk_control.risk import RiskControl
        rc = RiskControl.__new__(RiskControl)
        r = MagicMock()
        rc._redis = r
        rc._HALT_KEY = "risk:halted"
        rc._HALT_REASON_KEY = "risk:halt_reason"
        rc._HALT_TS_KEY = "risk:halt_ts"
        rc.emergency_halt("t")
        ts_keys = [c.args[0] for c in r.set.call_args_list]
        assert "risk:halted" in ts_keys and "risk:halt_ts" in ts_keys and "risk:halt_reason" in ts_keys
        rc.resume()
        r.delete.assert_called_with("risk:halted", "risk:halt_reason", "risk:halt_ts")
