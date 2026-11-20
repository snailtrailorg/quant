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
    ex.td_code = "td-xtp"
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
    ex.td_code = "td-xtp"
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
         patch("src.data_platform.config_store.exec_interface_codes", return_value=["td-xtp"]):
        desired = T._desired_units(conn)
    assert ("quant-td-exec@td-xtp.service", "td_exec") in desired


def test_sa4_units_scans_td_exec_pattern():
    """扫描面必须含 td-exec@*：漏则 active 被判缺失→每 300s 重复 start + 告警刷屏，
    且 failed 态进不了 78 配置错分流（sa4 对 td-exec 的恢复语义全失效）。"""
    from src.scheduler import tasks as T
    calls = []
    ok = MagicMock(returncode=0, stdout="")
    with patch.object(T, "_sa4_systemctl",
                      side_effect=lambda *a: calls.append(a) or ok):
        T._sa4_units("active")
    assert len(calls) == 1 and "quant-td-exec@*" in calls[0]


def test_sa4_stops_non_desired_td_exec():
    """账号禁用/删除后 td_exec 常驻进程随之停（否则 TD 会话遗留无人管）。"""
    from src.scheduler import tasks as T
    conn = MagicMock()
    conn.__enter__.return_value = conn
    calls = []
    with patch.object(T, "_sa4_units",
                      side_effect=lambda st: {"failed": [],
                                              "active": ["quant-td-exec@9.service"]}.get(st, [])), \
         patch.object(T, "_desired_units", return_value=[]), \
         patch.object(T, "get_conn", return_value=conn), \
         patch.object(T, "business_redis", side_effect=RuntimeError("no valkey")), \
         patch.object(T, "_sa4_systemctl",
                      side_effect=lambda *a: calls.append(a) or MagicMock(returncode=0)), \
         patch("src.alert_notify.notify"):
        T.sa4_reconciler()
    assert ("stop", "quant-td-exec@9.service") in calls


# ——— 停机纪律：必须带码硬退出 ———
# 钉反面：vnpy EventEngine 线程非 daemon → 仅 return 时进程不退出，
# systemd 等满 TimeoutStopSec(90s) SIGKILL → Result=timeout → OnFailure 误告警。

def test_shutdown_stops_engine_and_exits():
    from src.td_exec import main as M
    ex = M.TdExec.__new__(M.TdExec)
    ex.td_code = "td-xtp"
    ex._r = MagicMock()
    ex._close_thread = None
    ex._ee = MagicMock()
    with patch("src.td_exec.main.os._exit") as _exit:
        ex._shutdown()
    ex._ee.stop.assert_called_once()
    _exit.assert_called_once_with(0)
    ex._r.delete.assert_called_once()


def test_dispatch_paths_ensure_registry():
    """钉反面：web 进程 registry 未加载 → exec_capable_providers() 空 → 下发 0 账号但 200 done
    （prod 实录：一键清仓「成功」而 td_exec 没收到命令）。两条下发路径 + 监控期望集都必须先注册。"""
    import re as _re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src"
    for rel in ("web_api/routes/risk.py", "health_monitor/collector.py"):
        txt = (root / rel).read_text(encoding="utf-8")
        assert "ensure_registered()" in txt, f"{rel} 缺 ensure_registered（冷启动恒空账号集）"
        # 同时确认不是死代码：注册调用必须与 exec_interface_codes 同现
        assert "exec_interface_codes" in txt


# ——— 持仓快照 60s 刷新 ——
# 钉反面：live_task 退役后无人写 position_snapshot → Web 持仓页/风控快照年龄永久陈旧，
# 与清仓按实时 query_position 的判定不一致（prod 实录：页面 5 天旧仓 + 清仓零委托）。

def test_snapshot_cycle_cadence_and_gate():
    """账户快照与持仓同批刷新（snapshot_cycle）——缺账户快照则风控 fail-closed 拒所有平仓单。"""
    from src.td_exec import main as M
    ex = _mk_exec_for_window()
    ex._last_flush = 0.0
    ex._baseline_cache = {}
    ex._exec_port.connected = True
    with patch("src.strategy_runner.trading.snapshot_cycle") as sc:
        ex._maybe_flush_positions()
        ex._maybe_flush_positions()      # 60s 节流内不重复
    assert sc.call_count == 1
    # 未连不写（防断线空缓存覆盖真相表）
    ex2 = _mk_exec_for_window()
    ex2._last_flush = 0.0
    ex2._baseline_cache = {}
    with patch("src.strategy_runner.trading.snapshot_cycle") as sc2:
        ex2._maybe_flush_positions()
    sc2.assert_not_called()


# ——— 建连入参：tid 必须是 int ———
# 钉反面：session_slot._terminal_for 做 (tid-1)%98+2；传 str 在 prod 直接 TypeError 崩进程
# （单测全绿也看不见——此处断言实际调用参数类型）。

def test_build_passes_int_tid():
    """M5：tid 现由 code_terminal(code) 派生稳定整数（session_slot 做 (tid-1)%98+2 算术）。"""
    from src.td_exec import main as M
    ex = M.TdExec.__new__(M.TdExec)
    ex.td_code = "td-xtp"
    ex._quote_md_code = "md-xtp"
    ex._row = {"provider": "xtp", "impl": "plugin"}
    captured = {}
    rt = {"adapter": MagicMock(), "td_api": MagicMock(), "gw": MagicMock(),
          "exec_port": MagicMock()}

    def _fake(provider, ee, tid, td_code, boot_epoch, **kw):
        captured["tid"] = tid; captured["code"] = td_code
        return rt

    with patch("src.strategy_runner.main.make_event_engine", return_value=MagicMock()), \
         patch("src.strategy_runner.td_registry.code_terminal", return_value=2), \
         patch("src.strategy_runner.td_registry.build_td_runtime", side_effect=_fake), \
         patch("src.strategy_runner.trading.reconcile_orders"), \
         patch("src.strategy_runner.hub_worker.EmergencyWorker", return_value=MagicMock()):
        ex._build()
    assert isinstance(captured["tid"], int) and captured["tid"] == 2
    assert captured["code"] == "td-xtp"


# ——— 每日连接窗：盘外启动必须窗开沿建连 ———
# 钉反面：进程在窗关时启动 → _build 的 connect() 跳过；若主循环没有这一腿，
# 当日开盘零 TD 会话（下单全拒且进程无报错），systemd 凌晨重启即触发。

def _mk_exec_for_window():
    from src.td_exec import main as M
    ex = M.TdExec.__new__(M.TdExec)
    ex.td_code = "td-xtp"
    ex._td_window = (10, 10)
    ex._last_conn_ts = 0.0
    ex._exec_port = MagicMock(connected=False)
    ex._adapter = MagicMock()
    ex._conn_announced = False
    return ex


def test_window_open_connects_and_reconciles():
    ex = _mk_exec_for_window()
    ex._exec_port.connect.side_effect = lambda **kw: setattr(ex._exec_port, "connected", True)
    with patch("src.providers.xtp.session.xtp_session_window_open", return_value=True), \
         patch("src.providers.xtp.session.is_trading_day", return_value=True), \
         patch("src.strategy_runner.trading.reconcile_orders") as rec:
        ex._maybe_connect()      # 建连（XTP 登录异步：此时 connected 仍 False）
        ex._maybe_connect()      # 次 tick 观测到连上沿 → 对账一次
    ex._exec_port.connect.assert_called_once_with(force=True)
    rec.assert_called_once()


def test_port_connect_force_bypasses_stale_slot_snapshot():
    """钉反面：slot.window_open 是建槽时刻快照（窗关启动后恒 False）——
    补连必须走 force，否则 connect() 永远打日志跳过，当日零 TD 会话且无报错。"""
    from src.providers.xtp.td import XtpExecutionPort
    p = XtpExecutionPort.__new__(XtpExecutionPort)
    p.slot = SimpleNamespace(window_open=False)
    p._gw = MagicMock()
    p._setting = {"k": 1}
    XtpExecutionPort.connect(p)
    p._gw.connect.assert_not_called()
    XtpExecutionPort.connect(p, force=True)
    p._gw.connect.assert_called_once_with({"k": 1})


def test_window_closed_no_connect():
    ex = _mk_exec_for_window()
    with patch("src.providers.xtp.session.xtp_session_window_open", return_value=False), \
         patch("src.providers.xtp.session.is_trading_day", return_value=True):
        ex._maybe_connect()
    ex._exec_port.connect.assert_not_called()


def test_window_connected_skips_and_throttles():
    ex = _mk_exec_for_window()
    ex._exec_port.connected = True
    ex._conn_announced = True      # 已宣告过 → 不再对账
    with patch("src.providers.xtp.session.xtp_session_window_open", return_value=True), \
         patch("src.providers.xtp.session.is_trading_day", return_value=True):
        ex._maybe_connect()
    ex._exec_port.connect.assert_not_called()
    # 未连但 60s 节流窗内：不重试（防每 10s createTraderApi 循环）
    ex2 = _mk_exec_for_window()
    import time as _t
    ex2._last_conn_ts = _t.time()
    with patch("src.providers.xtp.session.xtp_session_window_open", return_value=True), \
         patch("src.providers.xtp.session.is_trading_day", return_value=True):
        ex2._maybe_connect()
    ex2._exec_port.connect.assert_not_called()


# ——— td→quote 迁移列 ———

def test_quote_account_via_td_md_map():
    """M5：取价映射由 td_md_map 承载（quote_account_id 列已退役）。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cols = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='td_md_map'").fetchall()}
    except Exception:
        pytest.skip("无 dev 库")
    assert {"td_code", "md_code", "priority"} <= cols


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

