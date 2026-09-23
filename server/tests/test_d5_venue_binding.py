"""D5 建任务与 worker 绑定测试：③时点 venue_allows + TD 网关注册表。"""
from unittest.mock import patch

import pytest

from src.risk_control.risk import RiskControl, RiskState


@pytest.fixture
def rc(monkeypatch):
    """独立实例（同 test_sb_risk）：is_halted/开关/market_op 均打桩放行。"""
    saved = RiskControl._instance
    c = RiskControl()
    RiskControl._instance = saved
    c.is_halted = lambda: False
    c.is_live_trading_allowed = lambda market: True
    c._role_of = staticmethod(lambda u: "admin")
    import src.data_platform.perms as _pm
    monkeypatch.setattr(_pm, "market_op_allowed", lambda u, r, m: True)
    return c


def _state(**kw):
    return RiskState(halted=False, total_drawdown=0.0, daily_loss=0.0, available=True, **kw)


BUY = {"symbol": "600000.SHSE", "action": "BUY", "volume": 100, "price": 10.0, "operator": "tester"}
SELL = {"symbol": "600000.SHSE", "action": "SELL", "volume": 100, "price": 10.0, "operator": "tester"}


class TestVenueAllowsCheckOrder:
    def test_buy_blocked_when_venue_not_allowed(self, rc, monkeypatch):
        """③时点：venue_allows=False 拦 BUY（D5 三级时点之三）。"""
        monkeypatch.setattr(rc, "_get_global_state", lambda vid: _state())
        monkeypatch.setattr("src.data_platform.perms.venue_allows", lambda vid, sym: False)
        d = rc.check_order(dict(BUY), venue_id=1)
        assert not d.approved and d.rule == "VENUE_NOT_ALLOWED"

    def test_buy_allowed_when_venue_allows(self, rc, monkeypatch):
        monkeypatch.setattr(rc, "_get_global_state", lambda vid: _state())
        monkeypatch.setattr("src.data_platform.perms.venue_allows", lambda vid, sym: True)
        d = rc.check_order(dict(BUY), venue_id=1)
        assert d.approved

    def test_sell_exempt(self, rc, monkeypatch):
        """③时点：SELL 豁免（venue_allows=False 不拦 SELL，D1 裁定）。"""
        monkeypatch.setattr(rc, "_get_global_state", lambda vid: _state())
        monkeypatch.setattr("src.data_platform.perms.venue_allows", lambda vid, sym: False)
        d = rc.check_order(dict(SELL), venue_id=1)
        assert d.approved


class TestTdRegistry:
    def test_xtp_builder_registered(self):
        """D5：TD 运行时构建注册表（禁 if provider== 硬编码，M3 守门立法）。"""
        import src.strategy_runner.main as main_mod
        assert "xtp" in main_mod._TD_BUILDERS
        assert callable(main_mod._TD_BUILDERS["xtp"])
