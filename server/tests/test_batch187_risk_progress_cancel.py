"""批187（v11）行为守门：统一进度看板 + 逐单 + 无限循环 + 取消 + 三源触发解耦 + 去重。

对应方案 `flow/方案/紧急处置账号校验-方案.md` v11。行为断言优先，覆盖失败路径。
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ——— 账号校验（裁定 A） ——

def test_skipped_account_no_400_and_reported():
    """无 running 任务的账号：不 400，计入 skipped_accounts 随回执明示。"""
    from src.web_api.routes import risk as R
    r = MagicMock()
    r.set.return_value = True
    r.get.side_effect = lambda k, **kw: None   # 无回执→走 dispatched，不空等由 wait_ack=False 控制
    with patch("src.data_platform.config_store.live_running_tasks",
               return_value=[("t1", 1)]), \
         patch("src.quant_common.redis_client.business_redis", return_value=r):
        out = R._dispatch_risk_cmd("cancel-all", {"username": "u"},
                                   accounts=[1, 999], wait_ack=False,
                                   username="u", operator_uid=7)
    assert out["status"] == "dispatched"
    assert 999 in out["skipped_accounts"], "无任务账号须进 skipped"
    assert out["dispatched"] == 1


def test_zombie_runner_still_dispatched():
    """僵尸 runner（DB running 即下发，不看心跳）——下发判活只认 live_running_tasks。"""
    from src.web_api.routes import risk as R
    r = MagicMock()
    with patch("src.data_platform.config_store.live_running_tasks",
               return_value=[("zombie", 5)]), \
         patch("src.quant_common.redis_client.business_redis", return_value=r):
        out = R._dispatch_risk_cmd("cancel-all", None, wait_ack=False,
                                   username="飞书:张三", operator_uid=None)
    assert out["dispatched"] == 1, "DB running 的僵尸仍须下发（心跳不参与判活）"


def test_no_match_account_400_vs_no_runner_409():
    """三态分叉：有 running 但勾选不命中=400；全局无 running=409。"""
    from src.web_api.errors import ApiError
    from src.web_api.routes import risk as R
    with patch("src.data_platform.config_store.live_running_tasks",
               return_value=[("t1", 1)]):
        with pytest.raises(ApiError) as e:
            R._dispatch_risk_cmd("cancel-all", None, accounts=[999],
                                 username="u", wait_ack=False)
        assert e.value.status_code == 400
    with patch("src.data_platform.config_store.live_running_tasks", return_value=[]):
        with pytest.raises(ApiError) as e:
            R._dispatch_risk_cmd("cancel-all", None, username="u", wait_ack=False)
        assert e.value.status_code == 409


# ——— 方案 A：payload 与执行参数解耦 ———

def test_dispatch_accepts_explicit_identity_without_payload():
    """非 web 源无 payload，靠显式 username/operator_uid 也能下发（方案 A 核心）。"""
    from src.web_api.routes import risk as R
    r = MagicMock()
    with patch("src.data_platform.config_store.live_running_tasks",
               return_value=[("t1", 1)]), \
         patch("src.quant_common.redis_client.business_redis", return_value=r):
        R._dispatch_risk_cmd("cancel-all", None, wait_ack=False,
                             username="飞书:张三", operator_uid=None)
    # cmd 载荷 operator 须为显式传入值，不从 payload（None）取
    cmd = json.loads(r.set.call_args_list[0].args[1])
    assert cmd["operator"] == "飞书:张三"
    assert cmd["operator_uid"] is None


# ——— 逐单状态 ———

def test_cancel_working_orders_returns_per_order_items():
    """撤单逐单：requested/failed/untranslatable 都进 items，计数兼容。"""
    from src.strategy_runner import trading as T
    a = MagicMock()
    ok = SimpleNamespace(symbol="600000.XSHG", vt_orderid="vt1", status="submitted")
    bad = SimpleNamespace(symbol="000001.XSHE", vt_orderid="vt2", status="submitted")
    a.query_orders.return_value = [ok, bad]
    a.cancel_order.side_effect = [None, RuntimeError("撤单失败")]
    from src.quant_common.domain import OrderStatus
    with patch.object(T, "status_of", return_value=OrderStatus.SUBMITTED):
        out = T._cancel_working_orders(a)
    # requested=受理数（2，含失败的那笔），failed=其中失败数（1）——原语义
    assert out["requested"] == 2 and out["failed"] == 1
    results = {it["result"] for it in out["items"]}
    assert results == {"requested", "failed"}
    assert all("symbol" in it for it in out["items"])


def test_merge_progress_items_preserves_status_semantics():
    """逐单归并：cancel/close 两类、status 各自语义不合并。"""
    from src.strategy_runner.trading import _merge_progress_items
    items = _merge_progress_items(
        [{"symbol": "A", "result": "untranslatable", "detail": "x"}],
        {"pending": ["B:100"], "no_quote": ["C:no_tick"], "rejected": ["D:拒"],
         "skipped_t1": ["E"], "closed_requested": 2},
    )
    kinds = {i["kind"] for i in items}
    assert kinds == {"cancel", "close"}
    statuses = {i["status"] for i in items}
    assert {"untranslatable", "pending", "no_quote", "rejected", "skipped", "closed_requested"} <= statuses


# ——— 无限循环 + 取消 ———

def test_next_action_no_count_limit():
    """无限循环：tries/empty_streak 不终止；仅 done/cancelled/systemic。"""
    from src.strategy_runner.trading import next_action
    base = {"pending": ["X:100"], "positions_remain": 1}
    assert next_action({**base, "tries": 999}) == "retry"
    assert next_action({**base, "empty_streak": 999}) == "retry"
    assert next_action(base, cancelled=True) == "final:cancelled"
    assert next_action({**base, "systemic_down": True}) == "final:systemic"
    assert next_action({"pending": [], "positions_remain": 0}) == "final:done"


def test_cancel_requested_and_key_format():
    """取消键判定：键在=True（never-throw）；键格式单点。"""
    from src.strategy_runner import trading as T
    r = MagicMock()
    r.get.return_value = "1"
    with patch("src.quant_common.redis_client.business_redis", return_value=r):
        assert T.cancel_requested("abc") is True
        r.get.assert_called_with(T.RISK_CANCEL_KEY.format(req_id="abc"))


def test_emergency_halt_publishes_persistent_trigger(monkeypatch):
    """三源统一触发：emergency_halt 置键后 LPUSH 持久化 List（非 pub/sub），带来源/去重元数据。"""
    from src.risk_control import risk as RC
    inst = RC.RiskControl.__new__(RC.RiskControl)
    r = MagicMock()
    inst._redis = r
    inst._HALT_KEY, inst._HALT_REASON_KEY, inst._HALT_TS_KEY = "h", "hr", "ht"
    inst.emergency_halt("飞书:张三")
    pushed = r.lpush.call_args.args[1]
    ev = json.loads(pushed)
    assert ev["source"] == "飞书"
    assert ev["sync_dispatched"] is False, "非 web 源须由统一派发，不跳过"
    assert ev["reason"] == "飞书:张三", "操作者显示在 reason"
    r.expire.assert_called()


def test_web_halt_marked_sync_dispatched():
    """web 源同步派发标记 True（halt_dispatch 跳过，避免双派发）。"""
    from src.risk_control import risk as RC
    inst = RC.RiskControl.__new__(RC.RiskControl)
    r = MagicMock()
    inst._redis = r
    inst._HALT_KEY, inst._HALT_REASON_KEY, inst._HALT_TS_KEY = "h", "hr", "ht"
    inst.emergency_halt("Web:alice", operator_uid=3)
    ev = json.loads(r.lpush.call_args.args[1])
    assert ev["source"] == "Web" and ev["sync_dispatched"] is True
    assert ev["operator_uid"] == 3


def test_halt_dispatch_skips_web_synced():
    """去抖闸：web 源 sync_dispatched=True → 消费者跳过（不重复撤单）。"""
    from src.scheduler.tasks import halt_dispatch
    r = MagicMock()
    r.lpop.return_value = json.dumps({"source": "Web", "sync_dispatched": True,
                                      "halt_ts": 1.0, "reason": "Web:alice"})
    with patch("src.scheduler.tasks.business_redis", return_value=r):
        out = halt_dispatch()
    assert out["status"] == "skip" and out["reason"] == "web_synced"


def test_halt_dispatch_dedup_and_dispatch():
    """非 web 源：SETNX 去重 + 写 cancel-all cmd（数据面，不 import web_api）。"""
    from src.scheduler.tasks import halt_dispatch
    r = MagicMock()
    r.lpop.return_value = json.dumps({"source": "飞书", "sync_dispatched": False,
                                      "halt_ts": 2.0, "reason": "飞书:张三",
                                      "operator_uid": 9})
    r.set.return_value = True   # SETNX 成功
    with patch("src.scheduler.tasks.business_redis", return_value=r), \
         patch("src.data_platform.config_store.live_running_tasks",
               return_value=[("t1", 1), ("t2", 1)]):
        out = halt_dispatch()
    assert out["status"] == "dispatched" and out["runners"] == 1, "同 account 去重"
    # 第一个 set 是去重键，找 verb=cancel-all 的 cmd set
    cmd = None
    for c in r.set.call_args_list:
        try:
            d = json.loads(c.args[1])
            if isinstance(d, dict) and d.get("verb") == "cancel-all":
                cmd = d
                break
        except (ValueError, TypeError, IndexError):
            continue
    assert cmd is not None and cmd["operator"] == "飞书:张三"


def test_route_auth_gate_for_new_endpoints():
    """3 新端点须挂 Depends（路由守门 test_route_auth_gate.py 全扫也覆盖此断言）。"""
    from src.web_api.routes import risk as R
    import inspect
    for fn in (R.risk_active, R.risk_progress, R.risk_cancel):
        sig = inspect.signature(fn)
        assert "Depends" in str(sig), f"{fn.__name__} 缺 Depends 包裹"
