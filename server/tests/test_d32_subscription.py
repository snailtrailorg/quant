"""批 172d P3 · 行情授权门（data_license）守门。"""
from __future__ import annotations

import pytest

from src.data_platform import specs
from src.data_platform.db import get_conn
from src.data_platform.subscription import denied_symbols, licensed


def _db_up() -> bool:
    try:
        with get_conn() as c:
            c.execute("SELECT 1 FROM security_master LIMIT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="真库不可用")


@pytest.fixture
def acc():
    with get_conn() as c:
        aid = c.execute("SELECT id FROM trading_account WHERE account_key IS NOT NULL "
                        "ORDER BY id LIMIT 1").fetchone()[0]
        prev = specs.get_data_license(aid)
    specs.update_data_license(aid, "")
    specs.invalidate()
    yield aid
    specs.update_data_license(aid, prev)
    specs.invalidate()


def test_empty_license_allows_all(acc):
    assert licensed(acc, "600000.SHSE") is True
    assert licensed(acc, "110000.SHSE") is True


def test_category_whitelist(acc):
    specs.update_data_license(acc, "convertible")
    specs.invalidate()
    assert licensed(acc, "110000.SHSE") is True
    assert licensed(acc, "600000.SHSE") is False
    assert denied_symbols(acc, {"110000.SHSE", "600000.SHSE"}) == {"600000.SHSE"}


def test_symbol_whitelist_and_star(acc):
    specs.update_data_license(acc, "600000.SHSE")
    specs.invalidate()
    assert licensed(acc, "600000.SHSE") is True
    assert licensed(acc, "600519.SHSE") is False
    specs.update_data_license(acc, "*")
    specs.invalidate()
    assert licensed(acc, "600519.SHSE") is True


def test_unknown_symbol_fail_closed(acc):
    specs.update_data_license(acc, "stock")
    specs.invalidate()
    assert licensed(acc, "NOPE.NOPE") is False


def test_missing_account_fail_closed():
    assert licensed(999999, "600000.SHSE") is False
