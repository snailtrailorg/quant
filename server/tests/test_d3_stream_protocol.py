"""D3 同源与流协议测试：流键 account-aware / 同源校验 / 暖机 source 过滤（批 66b：全市场 per-account 统一）。"""
from unittest.mock import MagicMock, patch

import pytest

import src.strategy_runner.hub_worker as hw


class TestCryptoProvider:
    def test_crypto_suffix(self):
        assert hw._crypto_provider("BTCUSDT.BINANCE") == "binance_perp"
        assert hw._crypto_provider("ETHUSDT.OKX") == "okx_perp"

    def test_astock_none(self):
        assert hw._crypto_provider("600000.SHSE") is None
        assert hw._crypto_provider("000001.SZSE") is None
        assert hw._crypto_provider("688001.SSE") is None


class TestStreamKey:
    """批 66b（D26）：全市场统一 per-account 键——A 股/加密同形，无市场分支。"""

    def test_astock_per_account(self):
        assert hw.bar_stream_key("600000.SHSE", account_id=1) == "hub:bars:1:600000.SHSE"
        assert hw.bar_stream_key("000001.SZSE", account_id=4) == "hub:bars:4:000001.SZSE"

    def test_crypto_per_account(self):
        assert hw.bar_stream_key("BTCUSDT.BINANCE", account_id=7) == "hub:bars:7:BTCUSDT.BINANCE"
        assert hw.bar_stream_key("ETHUSDT.OKX", account_id=9) == "hub:bars:9:ETHUSDT.OKX"

    def test_none_raises_no_bare_fallback(self):
        """批 66b 钉：account_id 必填——裸键形态退役（无「退回裸键」分支，防复活）。"""
        with pytest.raises(ValueError):
            hw.bar_stream_key("600000.SHSE", account_id=None)
        with pytest.raises(ValueError):
            hw.bar_stream_key("BTCUSDT.BINANCE", account_id=None)


class TestAccountMismatch:
    """批 66b：同源校验无市场跳过分支（A 股同样必须匹配 account_id——payload 契约保证字段在场）。"""

    def test_matching(self):
        assert hw._account_mismatch({"account_id": "1"}, 1) is False
        assert hw._account_mismatch({"account_id": "7"}, 7) is False

    def test_astock_cross_source_now_rejected(self):
        """批 66b 反转钉：A 股跳过分支已删——跨源/缺失 account_id 一律拒（旧形态恒 False）。"""
        assert hw._account_mismatch({}, 1) is True
        assert hw._account_mismatch({"account_id": "99"}, 1) is True
        assert hw._account_mismatch({"account_id": "8"}, 7) is True

    def test_account_id_none_fail_closed(self):
        """account_id=None（异常态）→ fail-closed（无法判定同源，拒——防吃错行情）。"""
        assert hw._account_mismatch({}, None) is True
        assert hw._account_mismatch({"account_id": "7"}, None) is True

    def test_missing_account_id(self):
        assert hw._account_mismatch({}, 7) is True

    def test_invalid_account_id(self):
        assert hw._account_mismatch({"account_id": "abc"}, 7) is True


class TestGetBarsSourceFilter:
    """db.get_bars 的 source 过滤：source 非 None 拼接 AND source=%s；None 不过滤。"""

    @staticmethod
    def _conn():
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = MagicMock()
        cur.description = [("symbol",), ("freq",), ("ts",), ("open",), ("high",), ("low",),
                           ("close",), ("volume",), ("amount",), ("adj_factor",), ("source",)]
        cur.fetchall.return_value = []
        conn.cursor.return_value.__enter__.return_value = cur
        return conn

    def test_source_appends_and_clause(self):
        import src.data_platform.db as db
        conn = self._conn()
        with patch.object(db, "get_conn", return_value=conn), patch.object(db, "ensure_table"):
            db.get_bars("X.BINANCE", "1min", None, None, source="binance_perp")
        sql = conn.cursor.return_value.__enter__.return_value.execute.call_args.args[0]
        assert "AND source = %s" in sql
        assert sql.index("AND source") < sql.index("ORDER BY")   # 双盲审 P0 回归钉：AND 必须在 ORDER BY 之前
        assert conn.cursor.return_value.__enter__.return_value.execute.call_args.args[1][-1] == "binance_perp"

    def test_no_source_no_clause(self):
        import src.data_platform.db as db
        conn = self._conn()
        with patch.object(db, "get_conn", return_value=conn), patch.object(db, "ensure_table"):
            db.get_bars("X.BINANCE", "1min", None, None)
        sql = conn.cursor.return_value.__enter__.return_value.execute.call_args.args[0]
        assert "AND source" not in sql
