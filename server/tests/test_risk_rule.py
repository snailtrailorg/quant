"""风控规则单测：RiskRule check 逻辑 + get_rule + load_rules_from_db。"""
from unittest.mock import patch, MagicMock


def test_max_position_rule_pass():
    from src.risk_control.risk_rule import MaxPositionRule
    rule = MaxPositionRule(max_pct=0.1)
    result = rule.check({"pct": 0.05}, {"position_pct": 0.03})
    assert result.approved is True


def test_max_position_rule_reject():
    from src.risk_control.risk_rule import MaxPositionRule
    rule = MaxPositionRule(max_pct=0.1)
    result = rule.check({"pct": 0.08}, {"position_pct": 0.05})  # 0.05+0.08=0.13 > 0.1
    assert result.approved is False
    assert "超持仓" in result.reason


def test_max_single_order_rule():
    from src.risk_control.risk_rule import MaxSingleOrderRule
    rule = MaxSingleOrderRule(max_amount=100000)
    assert rule.check({"amount": 50000}, {}).approved is True
    assert rule.check({"amount": 150000}, {}).approved is False


def test_daily_loss_rule():
    from src.risk_control.risk_rule import DailyLossLimitRule
    rule = DailyLossLimitRule(max_loss=50000)
    assert rule.check({}, {"today_loss": -30000}).approved is True
    assert rule.check({}, {"today_loss": -60000}).approved is False


def test_get_rule():
    from src.risk_control.risk_rule import get_rule
    rule = get_rule("max_position", {"max_pct": 0.2})
    assert rule is not None
    assert rule.get_params() == {"max_pct": 0.2}


def test_get_rule_unknown():
    from src.risk_control.risk_rule import get_rule
    assert get_rule("unknown_rule_xyz") is None


def test_load_rules_from_db_empty():
    """DB 无规则返回空 list"""
    from src.risk_control.risk_rule import load_rules_from_db
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = []
    mock_conn.execute.return_value = mock_cur
    with patch("src.data_platform.db.get_conn") as m:
        m.return_value.__enter__ = MagicMock(return_value=mock_conn)
        m.return_value.__exit__ = MagicMock(return_value=None)
        rules = load_rules_from_db()
    assert rules == []
