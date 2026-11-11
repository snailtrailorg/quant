"""批 172b · P1b 等价测试（旧实现 vs domain/端口链）。

覆盖：状态映射/只进不退、成交落库字段/幂等/NULL trade_ref、cid/vt 查询互斥、
撤单两字段判定、对账方向文案、端口订阅幂等/守卫/重启反查/cancel 解析/send 空回执。
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import threading
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from vnpy.trader.constant import Direction as VnDirection
from vnpy.trader.constant import Status

from src.quant_common.domain import Action, Direction, Order, OrderState, OrderStatus, OrderType, Trade
from src.strategy_runner import legacy_translate as LT
from src.strategy_runner import trading as NEW
from src.strategy_runner.legacy_exec_port import LegacyExecutionPort
from tests.fixtures import legacy_trading as OLD
from tests.fixtures.vnpy_events import account_data, order_data, trade_data


class _Cur:
    def __init__(self, row=None, rowcount=1):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class _Conn:
    def __init__(self, rows=None):
        self.calls = []
        self.commits = 0
        self._rows = list(rows or [])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), params))
        return _Cur(self._rows.pop(0) if self._rows else None)

    def commit(self):
        self.commits += 1


class _EE:
    def __init__(self):
        self.handlers = {}

    def register(self, event_type, handler):
        self.handlers.setdefault(event_type, []).append(handler)


def _adapter():
    a = MagicMock()
    a._lock = threading.Lock()
    a._vt2cid = {}
    a.account_id = 7
    a.get_vt_orderid.return_value = None
    a.query_orders.return_value = []
    a.query_trades.return_value = []
    a.query_position.return_value = []
    a.query_account.return_value = []
    a.send_order.return_value = ""
    return a


def _run(fn, raw=None, domain=None, adapter=None, rows=None):
    conn = _Conn(rows)
    with patch("src.data_platform.db.get_conn", return_value=conn):
        fn(domain if domain is not None else raw, adapter, "s1", "600000.SHSE")
    return conn


# ── 写库等价 ──────────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    (Status.PARTTRADED, "partial"),
    (Status.ALLTRADED, "all_traded"),
    (Status.CANCELLED, "cancelled"),
    (Status.REJECTED, "rejected"),
])
def test_status_mapping_equivalent(status, expected):
    a1, a2 = _adapter(), _adapter()
    raw = order_data(status=status)
    c_old = _run(OLD.write_order_status, raw=raw, adapter=a1, rows=[(11,)])
    c_new = _run(NEW.write_order_status, domain=LT.to_domain_order(raw), adapter=a2, rows=[(11,)])
    assert c_old.calls == c_new.calls
    assert c_new.calls[1][1] == (expected, 11, ["all_traded", "cancelled", "rejected"])


def test_non_advancing_status_no_db():
    for st in (Status.SUBMITTING, Status.NOTTRADED):
        raw = order_data(status=st)
        c_old = _run(OLD.write_order_status, raw=raw, adapter=_adapter())
        c_new = _run(NEW.write_order_status, domain=LT.to_domain_order(raw), adapter=_adapter())
        assert c_old.calls == c_new.calls == []


def test_trade_fields_and_idempotent_equivalent():
    raw = trade_data(tradeid="T1", vt_trade="TEST.T1", dt=None)
    c_old = _run(OLD.write_trade_log, raw=raw, adapter=_adapter(), rows=[None])
    c_new = _run(NEW.write_trade_log, domain=LT.to_domain_trade(raw), adapter=_adapter(), rows=[None])
    assert c_old.calls == c_new.calls
    insert = [c for c in c_new.calls if "INSERT INTO trade_log" in c[0]][0]
    assert insert[1][4] == "BUY" and insert[1][7] == "TEST.T1"


def test_empty_trade_ref_writes_null():
    raw = trade_data(tradeid="", vt_trade="")
    c_new = _run(NEW.write_trade_log, domain=LT.to_domain_trade(raw), adapter=_adapter(), rows=[None])
    insert = [c for c in c_new.calls if "INSERT INTO trade_log" in c[0]][0]
    assert insert[1][7] is None


def test_trade_cid_hit_without_row_does_not_fall_back_to_vt():
    raw = trade_data(tradeid="T1", vt_order="TEST.1")
    a = _adapter()
    a._vt2cid = {"TEST.1": "CID.1"}
    conn = _Conn([None])
    with patch("src.data_platform.db.get_conn", return_value=conn):
        NEW.write_trade_log(LT.to_domain_trade(raw), a, "s1", "600000.SHSE")
    selects = [c for c in conn.calls if "SELECT id, strategy_id" in c[0]]
    assert len(selects) == 1 and "client_order_id" in selects[0][0]


def test_trade_cid_empty_uses_vt():
    raw = trade_data(tradeid="T1", vt_order="TEST.1")
    conn = _Conn([None])
    with patch("src.data_platform.db.get_conn", return_value=conn):
        NEW.write_trade_log(LT.to_domain_trade(raw), _adapter(), "s1", "600000.SHSE")
    selects = [c for c in conn.calls if "SELECT id, strategy_id" in c[0]]
    assert len(selects) == 1 and "vt_orderid" in selects[0][0]


# ── 撤单/对账 ─────────────────────────────────────────────

def test_cancel_only_reads_status_and_vt():
    a = _adapter()
    a.query_orders.return_value = [
        SimpleNamespace(status=Status.NOTTRADED, vt_orderid="x.1"),
        SimpleNamespace(status=Status.ALLTRADED, vt_orderid="x.2"),
    ]
    out = NEW._cancel_working_orders(a)
    assert out["requested"] == 1 and out["failed"] == 0 and out["untranslatable"] == 0
    a.cancel_order.assert_called_once_with("x.1")


def test_cancel_missing_status_is_untranslatable():
    a = _adapter()
    a.query_orders.return_value = [SimpleNamespace(vt_orderid="x.1")]
    out = NEW._cancel_working_orders(a)
    assert out == {"requested": 0, "failed": 0, "untranslatable": 1}
    a.cancel_order.assert_not_called()


def test_reconcile_direction_text_uses_raw_value():
    a = _adapter()
    a.query_orders.return_value = [
        SimpleNamespace(status=Status.NOTTRADED, symbol="600000.SHSE",
                        direction=VnDirection.LONG, volume=100, price=9.0)]
    a.query_trades.return_value = []
    conn = _Conn([[]])
    with patch("src.data_platform.db.get_conn", return_value=conn), \
         patch.object(NEW, "_alert") as al:
        NEW.reconcile_orders(a, "s1", "600000.SHSE")
    assert "Long" in al.call_args.args[1]


# ── 端口 ─────────────────────────────────────────────────

def _port(ee, adapter, guard=None, order_event="eOrder", trade_event="eTrade"):
    gf = guard or (lambda name: (lambda fn: fn))
    return LegacyExecutionPort(ee, lambda: adapter, gf, order_event, trade_event)


def test_subscribe_is_idempotent_by_cb():
    ee, a = _EE(), _adapter()
    p = _port(ee, a)
    seen = []

    def cb(x):
        seen.append(x)

    p.subscribe_order(cb)
    p.subscribe_order(cb)
    assert len(ee.handlers["eOrder"]) == 1


def test_subscribe_translates_and_guard_contains_errors():
    ee, a = _EE(), _adapter()
    errors = []

    def guard(name):
        def deco(fn):
            def w(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    errors.append((name, type(e).__name__))
            return w
        return deco

    p = _port(ee, a, guard)
    got = []
    p.subscribe_order(got.append)
    handler = ee.handlers["eOrder"][0]
    handler(SimpleNamespace(data=order_data(status=Status.ALLTRADED)))
    assert isinstance(got[0], OrderState) and got[0].status == OrderStatus.ALL_TRADED
    bad = SimpleNamespace(status=None, vt_orderid="x")
    handler(SimpleNamespace(data=bad))          # 不得抛
    assert errors and errors[0][0] == "worker.on_order_status"
    assert len(got) == 1


def test_query_orders_recovers_client_id_from_db_after_restart():
    ee, a = _EE(), _adapter()
    a.query_orders.return_value = [order_data(status=Status.NOTTRADED)]
    p = _port(ee, a)
    conn = _Conn([("CID.DB",)])
    with patch("src.data_platform.db.get_conn", return_value=conn):
        out = p.query_orders()
    assert out[0].client_id == "CID.DB"
    assert "order_log" in conn.calls[0][0]


def test_cancel_resolves_vt_via_db_after_restart():
    ee, a = _EE(), _adapter()
    p = _port(ee, a)
    conn = _Conn([("VT.DB",)])
    with patch("src.data_platform.db.get_conn", return_value=conn):
        p.cancel("CID.1")
    a.cancel_order.assert_called_once_with("VT.DB")


def test_cancel_unresolved_raises_lookup_error():
    ee, a = _EE(), _adapter()
    p = _port(ee, a)
    conn = _Conn([None])
    with patch("src.data_platform.db.get_conn", return_value=conn):
        with pytest.raises(LookupError):
            p.cancel("CID.1")


def test_send_empty_receipt():
    ee, a = _EE(), _adapter()
    p = _port(ee, a)
    r = p.send(Order("600000.SHSE", Action.BUY, 100, 10.0, OrderType.LIMIT, "CID.1"))
    assert r.accepted is False and r.client_id == ""


def test_query_account_empty_none_and_connected_property():
    ee, a = _EE(), _adapter()
    p = _port(ee, a)
    assert p.query_account() is None
    assert p.connected is False
    a.query_account.return_value = [account_data()]
    assert p.query_account().balance == 1_000_000.0
    assert p.query_positions() == []


def test_early_event_with_unbound_adapter_does_not_raise():
    ee = _EE()
    p = LegacyExecutionPort(ee, lambda: None, lambda name: (lambda fn: fn), "eOrder", "eTrade")
    got = []
    p.subscribe_order(got.append)
    ee.handlers["eOrder"][0](SimpleNamespace(data=order_data(status=Status.ALLTRADED)))
    assert got[0].client_id == "" and got[0].status == OrderStatus.ALL_TRADED


def test_cancel_all_three_working_states():
    a = _adapter()
    a.query_orders.return_value = [
        SimpleNamespace(status=Status.SUBMITTING, vt_orderid="x.1"),
        SimpleNamespace(status=Status.NOTTRADED, vt_orderid="x.2"),
        SimpleNamespace(status=Status.PARTTRADED, vt_orderid="x.3"),
        SimpleNamespace(status=Status.ALLTRADED, vt_orderid="x.4"),
    ]
    out = NEW._cancel_working_orders(a)
    assert out == {"requested": 3, "failed": 0, "untranslatable": 0}
    assert [c.args[0] for c in a.cancel_order.call_args_list] == ["x.1", "x.2", "x.3"]


def test_cancel_missing_vt_is_untranslatable():
    a = _adapter()
    a.query_orders.return_value = [SimpleNamespace(status=Status.NOTTRADED)]
    out = NEW._cancel_working_orders(a)
    assert out == {"requested": 0, "failed": 0, "untranslatable": 1}
    a.cancel_order.assert_not_called()


def test_order_cid_with_empty_vt_queries_cid_only():
    a = _adapter()
    a._vt2cid = {"": "CID.9"}
    conn = _Conn([None])
    d = LT.to_domain_order(order_data(status=Status.ALLTRADED, vt=""), "CID.9")
    with patch("src.data_platform.db.get_conn", return_value=conn):
        NEW.write_order_status(d, a, "s1", "600000.SHSE")
    selects = [c for c in conn.calls if "SELECT id FROM order_log" in c[0]]
    assert len(selects) == 1 and "client_order_id" in selects[0][0]


def test_translate_passthrough_preserves_client_id():
    o = LT.to_domain_order(order_data(status=Status.ALLTRADED), "CID.O")
    assert LT.to_domain_order(o, "CID.N").client_id == "CID.N"
    t = LT.to_domain_trade(trade_data(tradeid="T1"))
    assert LT.to_domain_trade(t, "CID.T").client_id == "CID.T"


def test_main_subscribes_order_before_build():
    """时序合同：subscribe_order 必须在 build_td_runtime 之前（保住 EMT RESTART 重放窗口）。

    这是结构断言；adapter=None 的运行时行为由 test_early_event_with_unbound_adapter 覆盖。
    """
    from src.strategy_runner import main as M
    tree = ast.parse(textwrap.dedent(inspect.getsource(M._run_hub_mode)))
    subs = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "subscribe_order"]
    builds = [n.lineno for n in ast.walk(tree)
              if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "build_td_runtime"]
    assert subs and builds and min(subs) < min(builds)


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as c:
            c.execute("SELECT 1 FROM trade_log LIMIT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="真库不可用")
def test_trade_ref_unique_on_real_db():
    """真库幂等：同 trade_ref 连写两次 → 1 行（mock 只证 SQL 文本，不证唯一索引）。"""
    from src.data_platform.db import get_conn
    ref = "p1b-eq-" + uuid.uuid4().hex[:10]
    a = _adapter()
    a.account_id = None
    t = Trade(tradeid=ref, orderid="", symbol="600000.SHSE", direction=Direction.LONG,
              price=1.0, volume=1)
    try:
        NEW.write_trade_log(t, a, "p1b-eq", "600000.SHSE")
        NEW.write_trade_log(t, a, "p1b-eq", "600000.SHSE")
        with get_conn() as c:
            n = c.execute("SELECT count(*) FROM trade_log WHERE trade_ref=%s", (ref,)).fetchone()[0]
        assert n == 1
    finally:
        with get_conn() as c:
            c.execute("DELETE FROM trade_log WHERE trade_ref=%s", (ref,))
            c.commit()
