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


class TestActionClassification:
    """盲审A-P1：action 按 adjusted 语义判（原 "截断" in reason 误分类——场内截断拒单
    记 adjust、真覆写记 approve）。"""

    def _rc_amount(self, max_amount=500):
        c = _rc()
        c._role_of = staticmethod(lambda u: "trader")
        c._rules = {"global": {}, "etf_conv": {"max_single_amount": max_amount}, "crypto": {}}
        return c

    def test_truncate_to_zero_reject_is_reject(self):
        """场内"截断后 volume=0"=拒单——action 必须 reject 非 adjust。"""
        ins = MagicMock()
        ins.__enter__.return_value = ins
        c = self._rc_amount(max_amount=500)
        # price=600 × volume=1 → 600 超限 500 → new_vol=int(500/600)=0 → 拒单
        with patch("src.data_platform.perms.market_op_allowed", return_value=True), \
             patch("src.risk_control.risk.get_conn", return_value=ins):
            d = c.check_order({"symbol": "600000.SHSE", "action": "BUY", "volume": 1,
                               "price": 600.0, "operator": "u1"})
        assert not d.approved and d.rule == "MAX_SINGLE_AMOUNT"
        sql_params = ins.execute.call_args[0][1]
        # action 在参数首位；截断归零=reject
        assert sql_params[0] == "reject"

    def test_truncate_adjust_is_adjust(self):
        """真覆写（approved+adjusted）——action=adjust 且带 rule（溯源覆写成因）。"""
        ins = MagicMock()
        ins.__enter__.return_value = ins
        c = self._rc_amount(max_amount=500)
        # price=10 × volume=100 → 1000 超 500 → new_vol=50（截断放行=覆写）
        with patch("src.data_platform.perms.market_op_allowed", return_value=True), \
             patch("src.risk_control.risk.get_conn", return_value=ins):
            d = c.check_order(dict(BUY))
        assert d.approved and d.adjusted is not None and d.rule == "MAX_SINGLE_AMOUNT"
        assert ins.execute.call_args[0][1][0] == "adjust"


class TestRuleCodeCompleteness:
    """B-P2-1：规则码防漏网/防拼错的静态扫描钉子（无单一真相源前的护栏）。"""

    def test_every_reject_branch_tagged(self):
        import os, re
        src = open(os.path.join(os.path.dirname(__file__), "..", "src", "risk_control", "risk.py")).read()
        # 所有 approved=False 的构造块（跨行）必须带 rule=；先剥 f-string 内嵌 {...}（内含括号会截断块匹配）
        src_scannable = re.sub(r"\{[^}]*\}", "", src)
        blocks = re.findall(r"RiskDecision\(\s*approved=False.*?\)", src_scannable, re.S)
        assert len(blocks) >= 15
        for b in blocks:
            assert 'rule="' in b, f"拒单分支漏打码: {b[:80]}"

    def test_frontend_floor_codes_exist_in_backend(self):
        """前端估宽地板码 ⊆ 后端实际码集（跨文件一致性）。"""
        import os, re
        here = os.path.dirname(__file__)
        backend = open(os.path.join(here, "..", "src", "risk_control", "risk.py")).read()
        codes = set(re.findall(r'rule="([A-Z_]+)"', backend))
        assert len(codes) >= 15 and len(codes) == len({c for c in codes})  # 唯一性由 set 天然保证
        fe = open(os.path.join(here, "..", "..", "web", "src", "views", "Risk.vue")).read()
        floors = set(re.findall(r"'(MAX_DRAWDOWN|SNAPSHOT_UNAVAILABLE|[A-Z_]{6,})'", fe.split('logRule')[1][:300]))
        assert floors <= codes, f"前端地板码不在后端码集: {floors - codes}"
