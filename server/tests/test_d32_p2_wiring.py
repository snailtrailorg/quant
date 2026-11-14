"""批 172c P2b · 接线守门：place_order 合规挂点（拒绝必须不发单、同一 order_log 行标 rejected）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.quant_common.domain import Action
from src.risk_control.risk import RiskDecision
from src.strategy_framework.strategy import Signal, Strategy, StrategyConfig


class _Adapter:
    def __init__(self):
        self.sent = []

    def send_order(self, o):
        self.sent.append(o)
        return "CID.1"

    def get_vt_orderid(self, cid):
        return "VT.1"


def _strategy():
    cfg = StrategyConfig(id="s-wire", name="w", type="astock_analysis",
                         symbol="600000.XSHG", adapter="xtp")
    st = Strategy(cfg, _Adapter())
    st.account_id = None
    st.operator = "tester"
    return st


def _sig():
    return Signal(action=Action.BUY, symbol="600000.XSHG", volume=100, price=10.0, reason="t")


def _rules(state="fresh"):
    from src.data_platform.rules import RuleSet, _DummyAcc
    ins = SimpleNamespace(tick_size=0.01, price_band=None, lot_size=100, order_types=[],
                          sellable_rule="volume_minus_frozen", venue_flags={},
                          position_side="long_only", t_plus=1, order_range=None, symbol="600000.XSHG")
    return RuleSet(ms=None, ins=ins, acc=_DummyAcc({"opponent_depth": 1, "aggression": 0.1,
                                                    "max_order_qty": 100000}), state=state)


def _run(st, rules):
    statuses = []
    with patch("src.risk_control.risk.RiskControl.get",
               return_value=SimpleNamespace(check_order=lambda *a, **k: RiskDecision(approved=True, reason="ok"))), \
         patch.object(st, "_log_signal_order", return_value=(1, 101)), \
         patch.object(st, "_update_order_status", side_effect=lambda oid, s, **k: statuses.append((s, k))), \
         patch("src.data_platform.rules.rules_for", return_value=rules):
        st.place_order(_sig())
    return statuses


def test_rule_reject_blocks_send_and_marks_same_row():
    st = _strategy()
    statuses = _run(st, _rules("missing"))
    assert st.adapter.sent == [], "规则拒绝后不得发单"
    assert statuses and statuses[0][0] == "rejected"
    assert "RULE_MISSING" in statuses[0][1]["error"]


def test_rule_pass_sends_order():
    st = _strategy()
    statuses = _run(st, _rules("fresh"))
    assert len(st.adapter.sent) == 1
    assert [s for s, _ in statuses] == ["submitted"]


def test_rule_engine_failure_fails_closed():
    st = _strategy()
    with patch("src.risk_control.risk.RiskControl.get",
               return_value=SimpleNamespace(check_order=lambda *a, **k: RiskDecision(approved=True, reason="ok"))), \
         patch.object(st, "_log_signal_order", return_value=(1, 101)), \
         patch.object(st, "_update_order_status") as upd, \
         patch("src.data_platform.rules.rules_for", side_effect=RuntimeError("db down")):
        st.place_order(_sig())
    assert st.adapter.sent == []
    assert upd.call_args.args[1] == "rejected"
