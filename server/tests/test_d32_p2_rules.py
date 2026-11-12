"""批 172c P2b · 规则引擎/现金门/规格驱动定价（S4/S5/S9 面）。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.data_platform.db import get_conn
from src.data_platform.rules import (
    PROVIDER_CLOSE_DEFAULTS,
    RuleViolation,
    assert_dynamic_rules_complete,
    close_fallback_price,
    registered_categories,
    rules_for,
)
from src.data_platform.specs import InstrumentSpec
from src.strategy_framework.adapters import ExecutionAdapter


class _A(ExecutionAdapter):
    def send_order(self, o):
        return ""

    def cancel_order(self, vt):
        return None

    def query_position(self):
        return []


def _db_up() -> bool:
    try:
        with get_conn() as c:
            c.execute("SELECT 1 FROM security_master LIMIT 1")
        return True
    except Exception:
        return False


def _intent(**kw):
    base = dict(symbol="600000.SHSE", action="BUY", volume=100, price=10.0,
                order_type="limit", reason="")
    base.update(kw)
    return SimpleNamespace(**base)


# ── 四级兜底 ─────────────────────────────────────────────

def test_fallback_level1_limit_down():
    assert close_fallback_price({"lower_limit": 9.87, "bid": [10.0]}, "SELL",
                                {"aggression": 0.10}, True) == 9.87


def test_fallback_level2_opponent_with_aggression():
    tick = {"bid": [10.0], "ask": [10.1]}
    assert close_fallback_price(tick, "SELL", {"aggression": 0.10}, False) == pytest.approx(9.0)
    assert close_fallback_price(tick, "BUY", {"aggression": 0.10}, False) == pytest.approx(11.11)


def test_fallback_level3_last_price_clamped():
    tick = {"last_price": 9.0, "lower_limit": 9.5, "upper_limit": 11.0}
    assert close_fallback_price(tick, "SELL", {}, True) == 9.5
    tick2 = {"last_price": 12.0, "lower_limit": 0, "upper_limit": 11.0}
    assert close_fallback_price(tick2, "SELL", {}, True) == 11.0


def test_fallback_level4_none():
    assert close_fallback_price({}, "SELL", {}, False) is None


# ── RuleSet 语义 ─────────────────────────────────────────

def test_missing_rules_open_rejects_reduce_passes():
    rs = rules_for(None, "NOPE.NOPE")
    assert rs.state == "missing"
    with pytest.raises(RuleViolation) as ei:
        rs.validate(_intent(symbol="NOPE.NOPE"), {"last_price": 1.0}, "open")
    assert ei.value.code == "RULE_MISSING"
    rs.validate(_intent(symbol="NOPE.NOPE"), {"last_price": 1.0}, "reduce")   # 不抛


def test_dynamic_rules_registered():
    assert_dynamic_rules_complete()
    assert "convertible" in registered_categories()


@pytest.mark.skipif(not _db_up(), reason="真库不可用")
def test_lot_size_and_price_band_validation():
    rs = rules_for(None, "600000.SHSE")
    with pytest.raises(RuleViolation) as ei:
        rs.validate(_intent(volume=50), {"last_price": 10.0}, "open")
    assert ei.value.code == "LOT_SIZE"
    with pytest.raises(RuleViolation) as ei2:
        rs.validate(_intent(price=12.0), {"last_price": 10.0}, "open")
    assert ei2.value.code == "PRICE_BAND"
    rs.validate(_intent(volume=100, price=10.5), {"last_price": 10.0}, "open")   # 带内通过


# ── 规格驱动定价（S4）────────────────────────────────────

@pytest.mark.skipif(not _db_up(), reason="真库不可用")
def test_round_price_follows_security_master_tick():
    a = _A()
    assert a.round_close_price(10.126, {}, "600000.SHSE") == pytest.approx(10.13)
    assert a.round_close_price(100.1234, {}, "110000.SHSE") == pytest.approx(100.123)

    with get_conn() as c:
        prev = c.execute("SELECT tick_size FROM security_master WHERE vt_symbol='600000.SHSE'").fetchone()[0]
        c.execute("UPDATE security_master SET tick_size=0.05 WHERE vt_symbol='600000.SHSE'")
        c.commit()
    try:
        from src.data_platform import specs
        specs.invalidate()
        assert a.round_close_price(10.12, {}, "600000.SHSE") == pytest.approx(10.10)
    finally:
        with get_conn() as c:
            c.execute("UPDATE security_master SET tick_size=%s WHERE vt_symbol='600000.SHSE'", (prev,))
            c.commit()
        from src.data_platform import specs
        specs.invalidate()


@pytest.mark.skipif(not _db_up(), reason="真库不可用")
def test_adapter_uses_account_close_policy():
    from src.data_platform import specs
    with get_conn() as c:
        acc_id, prev = c.execute("SELECT id, params FROM trading_account ORDER BY id LIMIT 1").fetchone()
    try:
        specs.update_close_policy(acc_id, {"opponent_depth": 1, "aggression": 0.20, "max_order_qty": 500})
        a = _A()
        a.account_id = acc_id
        assert a.close_reference_price({"bid": [10.0], "ask": [10.1]},
                                       SimpleNamespace(symbol="600000.SHSE"), "SELL") == pytest.approx(8.0)
        assert a.max_close_order_qty("600000.SHSE") == 500
    finally:
        with get_conn() as c:
            c.execute("UPDATE trading_account SET params=%s::jsonb WHERE id=%s",
                      (json.dumps(prev or {}, ensure_ascii=False), acc_id))
            c.commit()
        specs.invalidate()


# ── 现金门（S9）───────────────────────────────────────────

class _RC:
    def __init__(self):
        from src.risk_control.risk import RiskControl
        self.rc = RiskControl()
        self.rc.is_halted = lambda: False
        self.rc.is_live_trading_allowed = lambda market: True
        self.rc._role_of = staticmethod(lambda u: "admin")
        self.rc._get_global_state = lambda vid: SimpleNamespace(
            halted=False, total_drawdown=0.0, daily_loss=0.0, available=True)


BUY = {"symbol": "600000.SHSE", "action": "BUY", "volume": 100, "price": 10.0, "operator": "t"}


@pytest.fixture
def rc(monkeypatch):
    import src.data_platform.perms as pm
    monkeypatch.setattr(pm, "market_op_allowed", lambda u, r, m: True)
    monkeypatch.setattr(pm, "account_allows", lambda vid, sym: True)
    return _RC().rc


def _snap(monkeypatch, cash, age_s=0.0, ts_none=False):
    from datetime import datetime, timedelta
    from src.data_platform.specs import AccountSnapshot
    ts = None if ts_none else datetime.now() - timedelta(seconds=age_s)
    monkeypatch.setattr("src.data_platform.specs.AccountSnapshot.of",
                        classmethod(lambda cls, aid: AccountSnapshot(aid, ts, cash, cash, 0.0, cash)))


def test_cash_gate_approves_enough(rc, monkeypatch):
    _snap(monkeypatch, 1e9)
    assert rc.check_order(dict(BUY), account_id=1).approved


def test_cash_gate_rejects_insufficient(rc, monkeypatch):
    _snap(monkeypatch, 500)
    d = rc.check_order(dict(BUY), account_id=1)
    assert not d.approved and d.rule == "CASH_INSUFFICIENT"


def test_cash_gate_rejects_missing_or_stale(rc, monkeypatch):
    _snap(monkeypatch, 1e9, ts_none=True)
    assert rc.check_order(dict(BUY), account_id=1).rule == "CASH_UNKNOWN"
    _snap(monkeypatch, 1e9, age_s=300)
    assert rc.check_order(dict(BUY), account_id=1).rule == "CASH_UNKNOWN"


def test_cash_gate_exempts_sell(rc, monkeypatch):
    _snap(monkeypatch, 0.0)
    sell = {**BUY, "action": "SELL"}
    assert rc.check_order(sell, account_id=1).approved


# ── 版本失效 ─────────────────────────────────────────────

def test_cfg_version_bump_and_specs_invalidate():
    from src.data_platform import specs
    from src.data_platform.routing import bump_config_version
    a = InstrumentSpec.of("600000.SHSE") if _db_up() else None
    v1 = bump_config_version()
    v2 = bump_config_version()
    assert v2 == v1 + 1
    specs.invalidate()
    if a is not None:
        assert InstrumentSpec.of("600000.SHSE") is not a
