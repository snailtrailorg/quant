"""D3 同源与流协议测试：流键 venue-aware / 同源校验 / 暖机 source 过滤。"""
from unittest.mock import MagicMock, patch

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
    def test_astock_no_venue_dimension(self):
        """A股单 hub：流键无 venue 维度（即便传了 venue_id 也忽略）。"""
        assert hw.bar_stream_key("600000.SHSE") == "hub:bars:600000.SHSE"
        assert hw.bar_stream_key("600000.SHSE", venue_id=1) == "hub:bars:600000.SHSE"

    def test_crypto_per_venue(self):
        assert hw.bar_stream_key("BTCUSDT.BINANCE", venue_id=7) == "hub:bars:7:BTCUSDT.BINANCE"
        assert hw.bar_stream_key("ETHUSDT.OKX", venue_id=9) == "hub:bars:9:ETHUSDT.OKX"

    def test_crypto_no_venue_falls_back_bare(self):
        """加密但无 venue_id（异常态）退回裸键——同源校验会在消费侧兜住。"""
        assert hw.bar_stream_key("BTCUSDT.BINANCE") == "hub:bars:BTCUSDT.BINANCE"


class TestVenueMismatch:
    def test_astock_never_mismatch(self):
        assert hw._venue_mismatch({}, "600000.SHSE", 1) is False
        assert hw._venue_mismatch({"venue_id": "99"}, "600000.SHSE", 1) is False

    def test_astock_venue_id_none_never_mismatch(self):
        assert hw._venue_mismatch({}, "600000.SHSE", None) is False

    def test_crypto_venue_id_none_fail_closed(self):
        """加密 + venue_id=None（异常态）→ fail-closed（无法判定同源，拒——防吃错行情）。"""
        assert hw._venue_mismatch({}, "BTCUSDT.BINANCE", None) is True

    def test_crypto_matching(self):
        assert hw._venue_mismatch({"venue_id": "7"}, "BTCUSDT.BINANCE", 7) is False

    def test_crypto_cross_source(self):
        """A venue worker 收到 venue_id=B 消息 → 跨源 fail-fast。"""
        assert hw._venue_mismatch({"venue_id": "8"}, "BTCUSDT.BINANCE", 7) is True

    def test_crypto_missing_venue_id(self):
        assert hw._venue_mismatch({}, "BTCUSDT.BINANCE", 7) is True

    def test_crypto_invalid_venue_id(self):
        assert hw._venue_mismatch({"venue_id": "abc"}, "BTCUSDT.BINANCE", 7) is True


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
