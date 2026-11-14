"""批 172d P3 · 插件路径集成（端口兼容面驱动急停全平）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.providers.xtp import _transport, td as TD
from src.strategy_framework.adapters import Order as LegacyOrder
from src.strategy_runner.session_slot import SessionSlot
from tests.fixtures.vnpy_events import position_data


class _EE:
    def __init__(self):
        self.handlers = {}

    def register(self, t, h):
        self.handlers.setdefault(t, []).append(h)


class _Gw:
    def __init__(self, ee=None, positions=None):
        self.ee = ee
        self._positions = positions or []
        self.sent = []
        self.cancelled = []

    def send_order(self, req):
        self.sent.append(req)
        return f"XTP.{len(self.sent)}"

    def cancel_order(self, req):
        self.cancelled.append(req)

    def query_position(self):
        if self.ee is not None:
            for p in self._positions:
                for h in self.ee.handlers.get(_transport.events()["position"], []):
                    h(SimpleNamespace(data=p))

    def query_account(self):
        pass


class _Api:
    connect_status = True


def _slot():
    return SessionSlot(1, "xtp", "td", 2, "t1:e1:", True, 1, "h", 1)


def _ready(fake_db=False):
    from src.risk_control.risk import RiskControl, RiskDecision

    rc = MagicMock()
    rc.check_order.return_value = RiskDecision(approved=True, reason="ok")
    return [
        patch("src.risk_control.risk.RiskControl.get", return_value=rc),
        patch("src.data_platform.hub_quote.get_retry_config",
              return_value={"retry_interval_s": 1, "retry_max": 1, "deadline_s": 5,
                            "no_quote_empty_max": 2}),
    ]


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as c:
            c.execute("SELECT 1 FROM order_log LIMIT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="真库不可用")
def test_close_all_via_plugin_compat():
    from src.strategy_runner import trading as T

    ee = _EE()
    from vnpy.trader.constant import Direction as VnDirection
    pos = position_data(symbol="600000.XSHG", volume=100, frozen=0, price=9.0,
                        direction=VnDirection.LONG)
    gw = _Gw(ee, [pos])
    port = TD.XtpExecutionPort(ee, gw, _Api(), _slot(), lambda n: (lambda f: f))
    compat = TD._LegacyCompat(port, account_id=None)

    patches = _ready()
    for p in patches:
        p.start()
    try:
        with patch.object(T, "_quote_price", return_value=({"bid": [10.0], "last_price": 10.0}, "")), \
             patch.object(T, "write_cmd_result"), patch.object(T, "_alert"), \
             patch("time.sleep"):
            out = T._close_all_positions(compat, account_id=None, operator="u", req_id="p3")
    finally:
        for p in patches:
            p.stop()

    assert out["closed_requested"] >= 1
    assert gw.sent and gw.sent[0].reference.startswith("emc-p3-")   # 全平自生成 cid
    from src.data_platform.db import get_conn
    with get_conn() as c:   # 清理本用例写入的 order_log
        c.execute("DELETE FROM order_log WHERE client_order_id LIKE %s", ("emc-p3-%",))
        c.commit()
