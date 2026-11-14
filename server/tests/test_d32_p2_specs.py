"""批 172c P2a · 规格层冒烟/真库断言。"""
from __future__ import annotations

import pytest

from src.data_platform.db import get_conn
from src.data_platform.specs import AccountPermSpec, AccountSnapshot, InstrumentSpec, MarketSpec


def _db_up() -> bool:
    try:
        with get_conn() as c:
            c.execute("SELECT 1 FROM security_master LIMIT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="真库不可用")


def test_market_spec_astock():
    ms = MarketSpec.of("astock")
    assert ms.tz and ms.calendar == "trade_cal"
    assert ms.sessions and any(s.get("phase") == "open" for s in ms.sessions)
    assert "bar" in ms.channels


def test_stock_instrument_spec():
    ins = InstrumentSpec.of("600000.XSHG")
    assert ins.category == "stock" and float(ins.tick_size) == 0.01
    assert ins.lot_size == 100 and ins.t_plus == 1
    assert ins.price_band and ins.price_band["normal_pct"] == 10.0
    assert ins.price_band["has_limit_band"] is True


def test_convertible_has_no_band_row():
    ins = InstrumentSpec.of("110000.XSHG")
    assert ins.category == "convertible" and float(ins.tick_size) == 0.001
    assert ins.t_plus == 0
    # 转债无 band_rules 行：price_band=None（由 tick.lower_limit 兜底，不能当 0）
    assert ins.price_band is None


def test_perp_defaults_and_unverified_flag():
    ins = InstrumentSpec.of("BTCUSDT.XBIN")
    assert ins.category == "perp" and ins.position_side == "both"
    assert ins.t_plus == 0 and ins.lot_size == 1
    assert ins.price_band is None
    assert ins.venue_flags.get("tick_unverified") is True


def test_missing_instrument_raises():
    with pytest.raises(KeyError):
        InstrumentSpec.of("NOPE.NOPE")


def test_account_perm_normalizes_etf():
    with get_conn() as c:
        row = c.execute("SELECT id FROM trading_account ORDER BY id LIMIT 1").fetchone()
    perm = AccountPermSpec.of(row[0])
    assert "etf" not in perm.allowed_categories
    assert "listed_fund" in perm.allowed_categories or perm.allowed_categories == set()


def test_missing_snapshot_returns_zeros():
    snap = AccountSnapshot.of(99999999)
    assert snap.ts is None and snap.available_cash == 0.0 and snap.equity == 0.0


def test_tick_normalized_in_db():
    with get_conn() as c:
        bad_stock = c.execute(
            "SELECT count(*) FROM security_master WHERE category='stock' AND tick_size<>0.01"
        ).fetchone()[0]
        bad_cb = c.execute(
            "SELECT count(*) FROM security_master WHERE category='convertible' AND tick_size<>0.001"
        ).fetchone()[0]
    assert bad_stock == 0 and bad_cb == 0


def test_close_policy_backfilled_to_account_params():
    with get_conn() as c:
        rows = c.execute(
            "SELECT provider, params->'close_policy' FROM trading_account "
            "WHERE params ? 'close_policy'").fetchall()
    assert rows
    for _provider, cp in rows:
        assert isinstance(cp, dict)
        assert isinstance(cp["opponent_depth"], int)
        assert 0 <= cp["aggression"] <= 0.5
        assert cp["max_order_qty"] >= 1
