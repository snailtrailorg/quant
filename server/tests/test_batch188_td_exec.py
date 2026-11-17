"""批188 守门：TD 执行进程化——形态X共同路径下单 / 主循环分流饥饿根治 / 账号集同源 /
号段注入 / td→quote 取价 / td_exec_down 监控。

钉「路径不可分辨」反面：分流 reject 必须留痕（回执+order_log）、共同路径风控不可旁路、
下发零判活不可静默丢单。
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ——— 命令解析 ———

def test_parse_cmd_normalizes():
    from src.td_exec.main import _parse_cmd
    raw = json.dumps({"id": "r1", "verb": "place-order", "operator": "u",
                      "order": {"symbol": "600000.XSHG"}, "signal_id": 7})
    c = _parse_cmd(raw)
    assert c["req_id"] == "r1" and c["verb"] == "place-order"
    assert c["order"]["symbol"] == "600000.XSHG" and c["signal_id"] == 7


def test_parse_cmd_bad_json_returns_none():
    from src.td_exec.main import _parse_cmd
    assert _parse_cmd("not-json{") is None


# ——— 主循环分流：close 期间 place-order 直接 reject（不投工作线程）———

def test_reject_writes_receipt_and_order_log():
    """分流 reject 必须写主回执 + order_log(rejected)（信号账不留孤儿）。"""
    from src.td_exec import main as M
    ex = M.TdExec.__new__(M.TdExec)
    ex.account_id = 1
    ex._r = MagicMock()
    nm = []
    with patch("src.strategy_runner.trading.write_cmd_result", side_effect=lambda rid, d, **k: nm.append((rid, d))) as wcr, \
         patch("src.data_platform.db.get_conn") as gc:
        conn = MagicMock()
        gc.return_value.__enter__ = lambda s: conn
        gc.return_value.__exit__ = lambda *a: False
        ex._reject({"req_id": "r1", "verb": "place-order", "signal_id": 7})
    # 回执写 rejected + 系统处置中
    assert nm and nm[0][0] == "r1" and nm[0][1]["accepted"] is False
    assert "处置中" in nm[0][1]["reason"]
    # order_log(rejected) 落库（signal_id 有值才写）
    assert conn.execute.called


def test_dispatch_routes_close_to_thread_and_place_to_main():
    """close-all 起线程（dispatch_closing 置位）；close 期 place 走 reject。"""
    from src.td_exec import main as M
    ex = M.TdExec.__new__(M.TdExec)
    ex.account_id = 1
    ex.dispatch_closing = __import__("threading").Event()
    ex.worker = MagicMock()
    started = []
    class _T:
        def __init__(self, target, args, name, daemon): self.target=target; self.args=args
        def start(self): started.append(self.args[0])
    with patch.object(M.threading, "Thread", _T):
        ex._dispatch({"verb": "close-all", "req_id": "c1"})
    assert ex.dispatch_closing.is_set() and started and started[0]["req_id"] == "c1"
    # close 进行中 → place 走 _reject（worker._handle 不被调用）
    with patch.object(ex, "_reject") as rj:
        ex._dispatch({"verb": "place-order", "req_id": "p1"})
    rj.assert_called_once()


# ——— 共同路径下单 execute_place_order ———

def test_execute_place_order_risk_reject_writes_order_log():
    from src.strategy_runner import trading as T
    adapter = MagicMock()
    with patch("src.risk_control.risk.RiskControl.get") as rcg, \
         patch.object(T, "_wal_insert_order", return_value=42) as wal:
        rcg.return_value.check_order.return_value = SimpleNamespace(approved=False, reason="HALTED", adjusted=None)
        out = T.execute_place_order(adapter, account_id=1,
                                    order={"symbol": "600000.XSHG", "action": "BUY", "volume": 100, "price": 10.0},
                                    signal_id=7)
    assert out["accepted"] is False and out["reason"] == "HALTED"
    wal.assert_called_once()   # reject 也写 order_log(rejected)
    adapter.send_order.assert_not_called()


def test_execute_place_order_adjusted_written_and_sent():
    from src.strategy_runner import trading as T
    adapter = MagicMock()
    adapter.send_order.return_value = "c1"
    adapter.get_vt_orderid.return_value = "v1"
    captured = {}
    def _wal(acc, order, sid, bf, status="submitting"):
        captured["order"] = order; captured["status"] = status; return 42
    with patch("src.risk_control.risk.RiskControl.get") as rcg, \
         patch.object(T, "_wal_insert_order", side_effect=_wal), \
         patch.object(T, "_update_order_status_row") as upd, \
         patch("src.data_platform.rules.rules_for") as rf:
        rcg.return_value.check_order.return_value = SimpleNamespace(
            approved=True, reason="ok",
            adjusted={"symbol": "600000.XSHG", "action": "BUY", "volume": 50, "price": 10.0, "order_type": "limit"})
        rf.return_value.validate.return_value = None
        out = T.execute_place_order(adapter, account_id=1,
                                    order={"symbol": "600000.XSHG", "action": "BUY", "volume": 100, "price": 10.0},
                                    signal_id=7)
    assert out["accepted"] is True and out["client_id"] == "c1"
    assert captured["order"]["volume"] == 50, "adjusted 值须进 WAL"
    adapter.send_order.assert_called_once()
    # submitted 回填
    assert any(c.args[1] == "submitted" for c in upd.call_args_list)


# ——— 账号集同源 ———

def test_exec_capable_providers_uses_execution_token():
    """具备 execution 能力的 provider 集（不是 market_data）。"""
    from src.registry import exec_capable_providers
    provs = exec_capable_providers()
    assert isinstance(provs, set)
    # 已注册的 provider 里，能 pick execution 的才在集合内（不硬编码，语义断言）
    from src.registry import DOMAIN_TRADING, UnsupportedCapability, pick, registered
    for _d, _p in registered():
        if _d != DOMAIN_TRADING:
            continue
        try:
            pick(_d, _p, {"execution"}, "plugin")
            assert _p in provs
        except UnsupportedCapability:
            assert _p not in provs


# ——— builder 号段注入 ———

def test_build_td_runtime_accepts_client_id():
    from src.strategy_runner import td_registry as R
    captured = {}
    def _fake_builder(ee, tid, account_id, boot_epoch, *, client_id=None, order_prefix=None):
        captured["cid"] = client_id; captured["prefix"] = order_prefix
        return {k: None for k in R._RT_KEYS}
    old = R.TD_BUILDERS.get("_t188")
    R.TD_BUILDERS["_t188"] = _fake_builder
    try:
        R.build_td_runtime("_t188", MagicMock(), "1", 1, 100, client_id=2, order_prefix="a1:e100:")
    finally:
        if old is None:
            R.TD_BUILDERS.pop("_t188", None)
        else:
            R.TD_BUILDERS["_t188"] = old
    assert captured["cid"] == 2 and captured["prefix"] == "a1:e100:"


# ——— SA4 第四源含 td_exec ———

def test_sa4_desired_includes_td_exec():
    from src.scheduler import tasks as T
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = []
    conn.execute.return_value = cur
    with patch.object(T, "_sa4_systemctl", return_value=None), \
         patch("src.data_platform.config_store.enabled_exec_accounts", return_value=[(4, None)]):
        desired = T._desired_units(conn)
    assert ("quant-td-exec@4.service", "td_exec") in desired


# ——— td→quote 迁移列 ———

def test_quote_account_column_exists():
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cols = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='trading_account'").fetchall()}
    except Exception:
        pytest.skip("无 dev 库")
    assert "quote_account_id" in cols


# ——— td_exec_down 监控 ———

def test_td_exec_down_rule():
    from src.health_monitor.monitor import evaluate
    findings, _ = evaluate({"td_execs": {"expected": [1, 2], "alive": [1]}})
    hits = [f for f in findings if f["rule_id"] == "td_exec_down"]
    assert hits and "2" in hits[0]["component"]


def test_td_exec_down_silent_when_all_alive():
    from src.health_monitor.monitor import evaluate
    findings, _ = evaluate({"td_execs": {"expected": [1], "alive": [1]}})
    assert not [f for f in findings if f["rule_id"] == "td_exec_down"]


# ——— 策略 live 发命令（形态X）———

def test_strategy_live_dispatches_cmd():
    from src.strategy_framework.strategy import Strategy, StrategyConfig, Signal, Action
    cfg = StrategyConfig(id="t188", name="t", type="astock_analysis", symbol="600000.XSHG", adapter="xtp")
    s = Strategy(cfg, None)
    s.account_id = 1
    s.operator = "u"
    s.paper_mode = False
    fake_r = MagicMock()
    with patch.object(s, "_log_signal_only", return_value=7), \
         patch("src.quant_common.redis_client.business_redis", return_value=fake_r):
        s.place_order(Signal(action=Action.BUY, symbol="600000.XSHG", volume=100, price=10.0, reason="t"))
    lc = fake_r.lpush.call_args_list
    assert lc and str(lc[0].args[0]) == "td:cmd:1"
    cmd = json.loads(lc[0].args[1])
    assert cmd["verb"] == "place-order" and cmd["signal_id"] == 7


# ——— 路由守门（新端点仍在，旧判活码已删）———

def test_risk_route_has_no_judgment_codes():
    import inspect
    from src.web_api.routes import risk
    src = inspect.getsource(risk._dispatch_risk_cmd)
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert "live_running_tasks" not in code
    assert "RISK_NO_ACTIVE_RUNNER" not in code and "RISK_NO_MATCH_ACCOUNT" not in code
    assert "td:cmd:" in code and "lpush" in code

