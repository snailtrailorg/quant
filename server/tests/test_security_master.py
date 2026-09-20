"""批 56a·M1：SMClient/MarketHours 测试（29 号 §四验收判据①——dev 真库行为级）。"""
import os
from datetime import date, datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("QUANT_TEST_NO_DB") == "1", reason="真库行为级验收")


@pytest.fixture(scope="module")
def sm():
    from src.data_platform.security_master import SMClient
    return SMClient()


@pytest.fixture(scope="module")
def mh():
    from src.data_platform.security_master import MarketHours
    return MarketHours()


class TestSMBackfill:
    """回填正确性（0092 后真库断言——任务验收①）。"""

    def test_stock_600000(self, sm):
        a = sm.get("600000.SHSE")
        assert a is not None
        assert a.category == "stock"
        assert a.multiplier == 100            # 股 100 股/手
        assert a.trade_phase == "T+1"
        assert a.session_id == "astock_main"
        assert a.market == "astock" and a.exchange == "SHSE"

    def test_convertible_t0(self, sm):
        a = sm.get("113531.SHSE")
        assert a is not None
        assert a.category == "convertible"
        assert a.multiplier == 10             # 转债 10 张/手（27 号勘误的根治位）
        assert a.trade_phase == "T+0"

    def test_etf(self, sm):
        a = sm.get("510300.SHSE")
        assert a is not None and a.category == "etf" and a.multiplier == 100

    def test_bse_backfilled(self, sm):
        a = sm.get("920726.BSE")
        assert a is not None and a.exchange == "BSE"

    def test_unknown_returns_none(self, sm):
        assert sm.get("NOPE.SHSE") is None

    def test_three_categories_present(self, sm):
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT count(DISTINCT category), count(*) FROM security_master")
            cats, total = cur.fetchone()
        assert cats >= 3 and total > 5000      # stock+etf+convertible 全回填


class TestSMState:
    def test_conv_price_effective(self, sm):
        v = sm.effective_attr("113531.SHSE", "conv_price", date(2026, 9, 19))
        assert v is not None and "conv_price" in v

    def test_unknown_state_none(self, sm):
        assert sm.effective_attr("600000.SHSE", "margin_tier", date(2026, 1, 1)) is None


class TestSMCovers:
    def test_astock_all_covers_stock(self, sm):
        from src.quant_common.contract import ASTOCK_ALL, ASTOCK_SHSE_SZSE
        assert sm.covers(ASTOCK_ALL, ("600000.SHSE",))
        assert not sm.covers(ASTOCK_SHSE_SZSE, ("430047.BSE",))   # 无 BSE 覆盖
        assert sm.covers(ASTOCK_SHSE_SZSE, ("600000.SHSE",))       # 库内档过滤


class TestMarketHours:
    def test_astock_seven_phases(self, mh):
        ps = mh.sessions("astock_main", date(2026, 9, 19))
        assert len(ps) == 7
        phases = {p.phase for p in ps}
        assert {"pre", "auction", "open", "lunch", "close_auct", "post_fix"} <= phases

    def test_crypto_one_phase(self, mh):
        assert len(mh.sessions("crypto_247", date(2026, 9, 19))) == 1

    def test_day_anchor(self, mh):
        assert mh.day_anchor("astock_main").hour == 15
        assert mh.day_anchor("crypto_247").hour == 0

    def test_is_auction_morning(self, mh):
        assert mh.is_auction("600000.SHSE", datetime(2026, 9, 18, 9, 22))
        assert not mh.is_auction("600000.SHSE", datetime(2026, 9, 18, 10, 0))

    def test_close_auction_scope(self, mh):
        # 深市 14:58 收盘竞价；沪市主板无（连续竞价至 15:00）
        assert mh.is_auction("125002.SZSE", datetime(2026, 9, 18, 14, 58))
        assert not mh.is_auction("600000.SHSE", datetime(2026, 9, 18, 14, 58))

    def test_band_rules(self, mh):
        assert mh.band_of("SHSE", "main", False) == 10
        assert mh.band_of("SHSE", "main", True) == 5      # ST 折半
        assert mh.band_of("SZSE", "chinext", False) == 20
        assert mh.band_of("BSE", "bse", False) == 30
