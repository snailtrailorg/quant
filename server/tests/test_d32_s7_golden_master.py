"""批 172d P3 · 插件注册/懒导入/S7 金标准（旧 XTPAdapter vs 新插件端口）。"""
from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest

from src.quant_common.domain import Action, Order as DomOrder, OrderType
from src.registry import UnsupportedCapability
from src.strategy_runner.session_slot import SessionSlot
from tests.fixtures.vnpy_events import order_data, trade_data


class _EE:
    def __init__(self):
        self.handlers = {}

    def register(self, t, h):
        self.handlers.setdefault(t, []).append(h)


class _Gw:
    def __init__(self):
        self.sent = []
        self.cancelled = []
        self.queried = []

    def send_order(self, req):
        self.sent.append(req)
        return f"XTP.{len(self.sent)}"

    def cancel_order(self, req):
        self.cancelled.append(req)

    def query_position(self):
        self.queried.append("pos")

    def query_account(self):
        self.queried.append("acc")


class _Api:
    connect_status = True


def _slot():
    return SessionSlot(account_id=1, provider="xtp", role="td", terminal_id=2,
                       order_prefix="t1:e1000:", window_open=True, tid=1,
                       holder="h", boot_epoch=1000)


def _port(ee=None, gw=None):
    from src.providers.xtp.td import XtpExecutionPort
    return XtpExecutionPort(ee or _EE(), gw or _Gw(), _Api(), _slot(),
                            lambda name: (lambda fn: fn), {"账号": "a"})


# ── 注册 / 懒导入 ────────────────────────────────────────

def test_import_providers_does_not_import_vnpy():
    """独立进程断言（同进程会被其他测试已导入的 vnpy 污染）。"""
    import pathlib
    import subprocess

    root = pathlib.Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-c", "import sys; import src.providers; "
                               "print(any(m == 'vnpy' or m.startswith('vnpy.') for m in sys.modules))"],
        cwd=root, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip() == "False", f"导入 providers 拉起了 vnpy: {out.stdout}"


def test_registry_registered_with_manifest():
    import src.providers  # noqa: F401
    from src.registry import get

    plugin = get("trading_account", "xtp")
    assert plugin.manifest.port_caps == {"execution", "market_data", "account"}
    with pytest.raises(ValueError):
        plugin.build({}, "td", _slot(), event_engine=None)
    with pytest.raises(UnsupportedCapability):
        plugin.build({}, "research", _slot())


# ── S7：下单/撤单等价 ────────────────────────────────────

def test_send_order_request_equivalent_to_legacy():
    from src.strategy_framework import adapters

    ee = _EE()
    g_old, g_new = _Gw(), _Gw()
    old = adapters.XTPAdapter(gateway=g_old, event_engine=ee, order_prefix="t1:e1000:")
    new = _port(_EE(), g_new)

    cid_old = old.send_order(adapters.Order(symbol="600000.SHSE", action="BUY", volume=100,
                                            price=10.0, order_type="limit", client_id="t1:e1000:c1"))
    r_new = new.send(DomOrder("600000.SHSE", Action.BUY, 100, 10.0, OrderType.LIMIT, "t1:e1000:c1"))

    assert cid_old == "t1:e1000:c1" == r_new.client_id and r_new.accepted is True
    a, b = g_old.sent[0], g_new.sent[0]
    for f in ("symbol", "exchange", "direction", "type", "volume", "price", "offset", "reference"):
        assert getattr(a, f) == getattr(b, f), f


def test_send_empty_orderid_rejected_equivalent():
    from src.strategy_framework import adapters
    from src.providers.xtp import td as TD

    class _Reject(_Gw):
        def send_order(self, req):
            return "XTP.0"

    old = adapters.XTPAdapter(gateway=_Reject(), event_engine=_EE(), order_prefix="p:")
    assert old.send_order(adapters.Order("600000.SHSE", "SELL", 100, 10.0)) is None

    port = TD.XtpExecutionPort(_EE(), _Reject(), _Api(), _slot(), lambda n: (lambda f: f))
    r = port.send(DomOrder("600000.SHSE", Action.SELL, 100, 10.0))
    assert r.accepted is False and r.reason


def test_cancel_prefers_cached_orderid():
    from src.providers.xtp import td as TD

    gw = _Gw()
    port = TD.XtpExecutionPort(_EE(), gw, _Api(), _slot(), lambda n: (lambda f: f))
    from vnpy.trader.constant import Exchange
    port._orders["XTP.1"] = SimpleNamespace(orderid="1", symbol="600000", exchange=Exchange.SSE)
    port._cid2vt["CID"] = "XTP.1"
    port.cancel("CID")
    assert gw.cancelled[0].orderid == "1" and gw.cancelled[0].symbol == "600000"


def test_cancel_unresolvable_raises():
    from src.providers.xtp import td as TD
    port = TD.XtpExecutionPort(_EE(), _Gw(), _Api(), _slot(), lambda n: (lambda f: f))
    with pytest.raises(LookupError):
        port.cancel("CID-NO-MAP")


# ── S7：翻译等价（新插件 translate vs P1b 迁移层）────────────────

def test_translate_equivalent_to_p1b():
    from src.providers.xtp import translate as T
    from src.strategy_runner import legacy_translate as L

    raw_o = order_data()
    assert T.to_order_state(raw_o) == L.to_domain_order(raw_o)
    raw_t = trade_data()
    assert T.to_trade(raw_t) == L.to_domain_trade(raw_t)


def test_subscribe_pushes_domain_and_recovers_client_id():
    from src.providers.xtp import td as TD
    ee = _EE()
    port = TD.XtpExecutionPort(ee, _Gw(), _Api(), _slot(), lambda n: (lambda f: f))
    port._cid2vt["CID.1"] = "TEST.1"
    port._vt2cid["TEST.1"] = "CID.1"
    got = []
    port.subscribe_order(got.append)
    from src.providers.xtp import _transport
    handler = ee.handlers[_transport.events()["order"]][-1]
    handler(SimpleNamespace(data=order_data()))
    assert got and got[0].client_id == "CID.1" and got[0].symbol == "600000.SHSE"

def test_md_port_delegates_to_gateway(monkeypatch):
    """MD 端口必须把 in_session/trading_day 原样下传（连接窗语义不丢）。"""
    import src.strategy_framework.md_gateway as G
    from src.registry import get

    seen = {}

    class FakeGw:
        provider = "xtp"

        def __init__(self, counters):
            self.counters = counters
            self.calls = []
            seen["gw"] = self

        @property
        def connected(self):
            return True

        def start_ready(self):
            return True

        def poll_supervise(self, in_session, trading_day):
            self.calls.append((in_session, trading_day))

        def subscribe(self, symbols):
            self.calls.append(("sub", tuple(symbols)))

        def unsubscribe(self, symbols):
            self.calls.append(("unsub", tuple(symbols)))

        def set_context(self, ctx):
            pass

        def connect(self, credentials, params):
            self.calls.append(("connect", credentials.get("k")))

        def close(self):
            pass

        def event_threads_healthy(self):
            return True

        def set_on_tick(self, cb):
            pass

        def snapshot(self, symbol):
            return None

    monkeypatch.setattr(G, "XtpMdGateway", FakeGw)
    port = get("trading_account", "xtp").build({}, "md", _slot())
    port.poll_supervise(True, False)
    port.subscribe(["600000.SHSE"])
    assert seen["gw"].calls[0] == (True, False)
    assert seen["gw"].calls[1] == ("sub", ("600000.SHSE",))
    assert port.connected is True
