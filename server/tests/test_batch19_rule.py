"""批19 · RiskDecision.rule 溯源测试。

覆盖：拒单分支打码（HALTED/MARKET_OP_DENIED/OPERATOR_MISSING/LIVE_SWITCH_OFF）、
放行 rule=None、risk_log INSERT 携带 rule 值（审计面端到端）。
夹具形态沿用 test_market_op.py（_rc 绕过外部依赖直击分支）。
"""
from unittest.mock import MagicMock, patch

from src.risk_control.risk import RiskControl, RiskState


def _rc():
    c = RiskControl()
    c.is_halted = lambda: False
    c.is_live_trading_allowed = lambda market: True
    c._get_global_state = lambda a: RiskState(halted=False, total_drawdown=0.0, daily_loss=0.0)
    return c


BUY = {"symbol": "600000.SHSE", "action": "BUY", "volume": 100, "price": 10.0, "operator": "u1"}
SELL = {"symbol": "600000.SHSE", "action": "SELL", "volume": 100, "price": 10.0, "operator": "u1"}


class TestRuleCodes:
    def test_halted_tagged(self):
        c = _rc()
        c.is_halted = lambda: True
        c.halt_reason = lambda: "手动熔断"
        d = c.check_order(dict(BUY))
        assert not d.approved and d.rule == "HALTED"

    def test_live_switch_off_tagged(self):
        c = _rc()
        c.is_live_trading_allowed = lambda market: False
        d = c.check_order(dict(BUY))
        assert not d.approved and d.rule == "LIVE_SWITCH_OFF"

    def test_market_op_denied_tagged(self):
        c = _rc()
        c._role_of = staticmethod(lambda u: "trader")
        ins = MagicMock()
        ins.__enter__.return_value = ins
        with patch("src.data_platform.perms.market_op_allowed", return_value=False), \
             patch("src.risk_control.risk.get_conn", return_value=ins):
            d = c.check_order(dict(BUY))
        assert not d.approved and d.rule == "MARKET_OP_DENIED"

    def test_missing_operator_tagged(self):
        c = _rc()
        ins = MagicMock()
        ins.__enter__.return_value = ins
        o = dict(BUY); del o["operator"]
        with patch("src.risk_control.risk.get_conn", return_value=ins):
            d = c.check_order(o)
        assert not d.approved and d.rule == "OPERATOR_MISSING"

    def test_sell_passthrough_rule_none(self):
        c = _rc()
        with patch("src.data_platform.perms.market_op_allowed", return_value=False):
            d = c.check_order(dict(SELL))
        assert d.approved and d.rule is None

    def test_risk_log_insert_carries_rule(self):
        """审计面端到端：rule 值随 risk_log INSERT 参数落库（非仅 dataclass 装饰）。"""
        c = _rc()
        c.is_halted = lambda: True
        c.halt_reason = lambda: "手动熔断"
        ins = MagicMock()
        ins.__enter__.return_value = ins
        with patch("src.risk_control.risk.get_conn", return_value=ins):
            d = c.check_order(dict(BUY))
        params = ins.execute.call_args[0][1]
        assert params[2] == "HALTED"   # INSERT (action, symbol, rule, detail, severity) 第三位
