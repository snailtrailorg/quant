"""批 173 手动买卖接口守门（行为级，全 mock，不连 DB/Valkey/网关）。

钉「路径不可分辨」反面：旁路风控 / WAL 时序 / 虚报成功 / 污染急停终局 / 静默丢命令。
"""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import pytest

from src.web_api.errors import ApiError
from src.web_api.routes import risk as risk_routes
from src.strategy_runner import trading, hub_worker


GOOD = {"account_id": 1, "symbol": "600000.SSE", "action": "BUY",
        "volume": 500, "price": 10.38, "confirm": "CONFIRM"}


def _payload():
    return {"username": "admin", "sub": "1", "role": "admin"}


# ——— 参数校验：非法请求不得触碰命令通道 ———

@pytest.mark.parametrize("bad,code", [
    ({"confirm": "NO"}, "MANUAL_CONFIRM_REQUIRED"),
    ({"account_id": 0}, "BAD_ACCOUNT_ID"),
    ({"symbol": "BAD"}, "BAD_SYMBOL"),
    ({"action": "HOLD"}, "BAD_ACTION"),
    ({"volume": 150}, "BAD_VOLUME"),
    ({"volume": 0}, "BAD_VOLUME"),
    ({"price": 0}, "BAD_PRICE"),
    ({"price": -1}, "BAD_PRICE"),
    ({"client_order_id": "bad id!"}, "BAD_CLIENT_ORDER_ID"),
])
def test_invalid_params_never_dispatch(bad, code):
    body = {**GOOD, **bad}
    with patch.object(risk_routes, "_dispatch_manual_cmd") as disp:
        with pytest.raises(ApiError) as e:
            risk_routes.manual_order(body=body, payload=_payload())
        assert e.value.code == code
        disp.assert_not_called()


# ——— 无活 runner：409 且命令未下发 ———

def test_no_active_runner_409():
    with patch.object(risk_routes, "_live_runners", return_value=[]), \
         patch.object(risk_routes, "audit_log"), \
         patch("src.quant_common.redis_client.business_redis") as br:
        with pytest.raises(ApiError) as e:
            risk_routes._dispatch_manual_cmd(GOOD, _payload())
        assert e.value.code == "MANUAL_NO_ACTIVE_RUNNER"
        br.return_value.set.assert_not_called()


# ——— 下发：命令键带 order 子对象、verb 正确、审计留痕 ———

def test_dispatch_writes_cmd():
    fake_r = MagicMock()
    fake_r.get.return_value = None
    with patch.object(risk_routes, "_live_runners", return_value=[("19", 1)]), \
         patch.object(risk_routes, "audit_log") as al, \
         patch("src.quant_common.redis_client.business_redis", return_value=fake_r):
        out = risk_routes._dispatch_manual_cmd(GOOD, _payload())
    assert out["status"] == "dispatched"
    key, raw = fake_r.set.call_args_list[0].args
    assert key == "risk:cmd:19"
    import json
    cmd = json.loads(raw)
    assert cmd["verb"] == "manual-order"
    assert cmd["order"]["symbol"] == "600000.SSE"
    assert cmd["order"]["volume"] == 500
    assert cmd["operator"] == "admin"
    assert al.called


# ——— 重复 client_order_id：400，不下发 ———

def test_duplicate_cid_400():
    fake_r = MagicMock()
    fake_r.set.return_value = False   # nx 失败
    body = {**GOOD, "client_order_id": "mo-fixed-1"}
    with patch.object(risk_routes, "_live_runners", return_value=[("19", 1)]), \
         patch("src.quant_common.redis_client.business_redis", return_value=fake_r), \
         patch.object(risk_routes, "audit_log"):
        with pytest.raises(ApiError) as e:
            risk_routes._dispatch_manual_cmd(body, _payload())
        assert e.value.code == "MANUAL_DUPLICATE_CID"
        # 只有去重键的 set，没有命令键
        assert all("risk:cmd:" not in c.args[0] for c in fake_r.set.call_args_list)


# ——— 必过风控：拒单时零 send_order、零 WAL ———

def _fake_adapter(send_ret="vt-1"):
    a = MagicMock()
    a.send_order.return_value = send_ret
    a.get_vt_orderid.return_value = "vt-1"
    a.query_position.return_value = []
    return a


def test_rejected_by_risk_never_sends():
    a = _fake_adapter()
    dec = NS(approved=False, reason="HALTED", adjusted=None)
    with patch("src.risk_control.risk.RiskControl.get") as get, \
         patch.object(trading, "manual_order_open") as wal:
        get.return_value.check_order.return_value = dec
        out = trading.submit_manual_order(a, 1, "admin", "r1", dict(GOOD))
    assert out["accepted"] is False
    a.send_order.assert_not_called()
    wal.assert_not_called()
    # 且 order dict 带 account_id / reason / operator（服务端钉死）
    od = get.return_value.check_order.call_args.args[0]
    assert od["account_id"] == 1 and od["reason"] == "manual_order"


# ——— WAL 先于 send（F-4 时序） ———

def test_wal_before_send():
    a = _fake_adapter()
    calls = []

    def wal(*args, **kwargs):
        calls.append("wal")
        return 42

    def send(order):
        calls.append("send")
        return "vt-1"

    a.send_order.side_effect = send
    dec = NS(approved=True, adjusted=None)
    with patch("src.risk_control.risk.RiskControl.get") as get, \
         patch.object(trading, "manual_order_open", side_effect=wal), \
         patch.object(trading, "_close_order_mark"):
        get.return_value.check_order.return_value = dec
        out = trading.submit_manual_order(a, 1, "admin", "r1", dict(GOOD))
    assert out["accepted"] is True
    assert calls == ["wal", "send"]


# ——— WAL 失败 / send 返 None：不虚报成功 ———

def test_wal_failed_no_send():
    a = _fake_adapter()
    dec = NS(approved=True, adjusted=None)
    with patch("src.risk_control.risk.RiskControl.get") as get, \
         patch.object(trading, "manual_order_open", return_value=None):
        get.return_value.check_order.return_value = dec
        out = trading.submit_manual_order(a, 1, "admin", "r1", dict(GOOD))
    assert out["accepted"] is False and out["reason"] == "WAL_FAILED"
    a.send_order.assert_not_called()


def test_send_none_marks_send_failed():
    a = _fake_adapter(send_ret=None)
    dec = NS(approved=True, adjusted=None)
    with patch("src.risk_control.risk.RiskControl.get") as get, \
         patch.object(trading, "manual_order_open", return_value=42), \
         patch.object(trading, "_close_order_mark") as mark:
        get.return_value.check_order.return_value = dec
        out = trading.submit_manual_order(a, 1, "admin", "r1", dict(GOOD))
    assert out["accepted"] is False and out["reason"] == "NOT_SENT"
    assert mark.call_args.args[3] == "send_failed"


# ——— SELL 可卖仓截断（T+1 冻结） ———

def test_sell_truncated_to_sellable():
    a = _fake_adapter()
    a.query_position.return_value = [NS(symbol="600000.SSE", volume=1000, frozen=400)]
    dec = NS(approved=True, adjusted=None)
    body = {**GOOD, "action": "SELL", "volume": 1000}
    with patch("src.risk_control.risk.RiskControl.get") as get, \
         patch.object(trading, "manual_order_open", return_value=42), \
         patch.object(trading, "_close_order_mark"):
        get.return_value.check_order.return_value = dec
        out = trading.submit_manual_order(a, 1, "admin", "r1", body)
    assert out["volume"] == 600
    assert a.send_order.call_args.args[0].volume == 600


def test_sell_no_sellable_no_send():
    a = _fake_adapter()
    a.query_position.return_value = [NS(symbol="600000.SSE", volume=500, frozen=500)]
    body = {**GOOD, "action": "SELL"}
    with patch("src.risk_control.risk.RiskControl.get") as get:
        out = trading.submit_manual_order(a, 1, "admin", "r1", body)
    assert out["accepted"] is False and out["reason"] == "NO_SELLABLE_POSITION"
    a.send_order.assert_not_called()
    get.return_value.check_order.assert_not_called()


# ——— 命令透传：order 子对象不得被白名单吞掉（禁静默 no-op） ———

def test_consume_risk_cmd_passes_order():
    import json
    raw = json.dumps({"id": "r1", "verb": "manual-order", "operator": "admin",
                      "order": {"symbol": "600000.SSE", "action": "BUY",
                                "volume": 500, "price": 10.38}})
    fake_r = MagicMock()
    fake_r.getdel.return_value = raw
    q = MagicMock()
    hub_worker.consume_risk_cmd(fake_r, None, "19", cmd_queue=q)
    item = q.put_nowait.call_args.args[0]
    assert item["verb"] == "manual-order"
    assert item["order"]["volume"] == 500


# ——— manual-order 不写 :final / 不发 SSE（不污染急停终局） ———

def test_manual_order_branch_no_final():
    w = hub_worker.EmergencyWorker(adapter=MagicMock(), td_api=MagicMock(),
                                   account_id=1, tid="19",
                                   closing=__import__("threading").Event())
    cmd = {"req_id": "r1", "verb": "manual-order", "operator": "admin",
           "order": {"symbol": "600000.SSE", "action": "BUY", "volume": 500,
                     "price": 10.38}}
    with patch.object(w, "_manual_order", return_value={"accepted": True}), \
         patch.object(w, "_final") as final, \
         patch.object(trading, "write_cmd_result") as wcr:
        w._handle(cmd)
    final.assert_not_called()
    assert wcr.called
    assert wcr.call_args.args[1]["phase"] == "done"
