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
    for m in [m for m in sys.modules if m == "vnpy" or m.startswith("vnpy.")]:
        del sys.modules[m]
    import importlib

    import src.providers as P
    importlib.reload(P)
    assert not [m for m in sys.modules if m.startswith("vnpy")]


def test_registry_registered_with_manifest():
    import src.providers  # noqa: F401
    from src.registry import get

    plugin = get("trading_account", "xtp")
    assert plugin.manifest.port_caps == {"execution", "market_data", "account"}
    with pytest.raises(UnsupportedCapability):
        plugin.build({}, "md", _slot())
    with pytest.raises(ValueError):
        plugin.build({}, "td", _slot(), event_engine=None)


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
