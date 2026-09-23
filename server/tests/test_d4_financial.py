"""D4 金融面交易可靠性测试：费率计算 / symbol 分类 / 敞口净敞口。"""
from unittest.mock import MagicMock, patch

from src.quant_common.fees import calc_trade_fee, symbol_market, COMMISSION_RATE, STAMP_TAX, TRANSFER_FEE


class TestSymbolMarket:
    def test_astock(self):
        assert symbol_market("600000.SHSE") == "astock"
        assert symbol_market("000001.SZSE") == "astock"

    def test_convertible(self):
        assert symbol_market("110001.SHSE") == "convertible"   # 沪转债 11
        assert symbol_market("123001.SZSE") == "convertible"   # 深转债 12

    def test_etf(self):
        assert symbol_market("510300.SHSE") == "etf"   # 沪 ETF
        assert symbol_market("159915.SZSE") == "etf"   # 深 ETF

    def test_crypto(self):
        assert symbol_market("BTCUSDT.BINANCE") == "binance_perp"
        assert symbol_market("BTCUSDT.OKX") == "okx_perp"


class TestCalcTradeFee:
    def test_astock_buy_commission_plus_transfer(self):
        """A股买入：佣金 + 过户费（双边），无印花。"""
        amount = 100_000.0
        fee = calc_trade_fee("600000.SHSE", "BUY", amount)
        assert abs(fee - (amount * COMMISSION_RATE + amount * TRANSFER_FEE)) < 1e-9

    def test_astock_sell_includes_stamp(self):
        """A股卖出：佣金 + 过户费 + 印花税。"""
        amount = 100_000.0
        fee = calc_trade_fee("600000.SHSE", "SELL", amount)
        assert abs(fee - (amount * COMMISSION_RATE + amount * TRANSFER_FEE + amount * STAMP_TAX)) < 1e-9

    def test_convertible_exempt_stamp_transfer(self):
        """可转债：免印花免过户，仅佣金。"""
        amount = 100_000.0
        fee = calc_trade_fee("110001.SHSE", "SELL", amount)
        assert abs(fee - amount * COMMISSION_RATE) < 1e-9

    def test_etf_exempt_stamp_transfer(self):
        """场内基金：免印花免过户，仅佣金。"""
        amount = 100_000.0
        fee = calc_trade_fee("510300.SHSE", "SELL", amount)
        assert abs(fee - amount * COMMISSION_RATE) < 1e-9

    def test_zero_amount(self):
        assert calc_trade_fee("600000.SHSE", "BUY", 0) == 0.0


class TestNetExposure:
    def test_sql_has_net_exposure_case(self):
        """D4：_symbol_exposure 净敞口——direction_long 正 / direction_short 负。"""
        import src.data_platform.db as db_mod
        from src.risk_control.risk import RiskControl
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchone.return_value = (0,)
        with patch.object(db_mod, "get_conn", return_value=conn):
            RiskControl._symbol_exposure(MagicMock(), "600000.SHSE", 1)
        sql = conn.execute.call_args_list[0].args[0]
        assert "CASE WHEN direction='direction_long'" in sql
        assert "WHEN direction='direction_short' THEN -cost_price*volume" in sql
