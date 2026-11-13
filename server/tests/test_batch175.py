"""批 175 L1 守门：translate 三态 vnpy 口径 → 项目 vt（.SSE→.SHSE）。"""
from types import SimpleNamespace as NS

from src.providers.xtp import translate

_ST = NS(name="ALLTRADED")


def test_position_sse_to_shse():
    p = NS(vt_symbol="600036.SSE", symbol="600036", direction=None,
           volume=800, price=44.2, pnl=0.0, frozen=0, yd_volume=0)
    assert translate.to_position(p).symbol == "600036.SHSE"


def test_order_state_bare_code_with_exchange():
    """真 vnpy Order 形状：无 vt_symbol，只有 symbol(裸码)+exchange.value —— 不能抛。"""
    d = NS(symbol="600036", exchange=NS(value="SSE"), orderid="1", vt_orderid="vt1",
           direction=None, status=_ST, volume=100, traded=100, price=44.0, datetime=None)
    assert translate.to_order_state(d).symbol == "600036.SHSE"


def test_trade_bare_code_with_exchange():
    d = NS(symbol="000001", exchange=NS(value="SZSE"), vt_tradeid="t1", vt_orderid="o1",
           direction=None, price=10.0, volume=100, datetime=None)
    assert translate.to_trade(d).symbol == "000001.SZSE"


def test_crypto_passthrough_not_raised():
    d = NS(vt_symbol="BTCUSDT.BINANCE", symbol="BTCUSDT", exchange=None, orderid="", vt_orderid="",
           direction=None, status=_ST, volume=1, traded=0, price=1, datetime=None)
    assert translate.to_order_state(d).symbol == "BTCUSDT.BINANCE"


def test_empty_symbol_kept_empty():
    d = NS(symbol="", exchange=None, orderid="", vt_orderid="", direction=None, status=_ST,
           volume=0, traded=0, price=0, datetime=None)
    assert translate.to_order_state(d).symbol == ""


def test_flush_positions_writes_canonical():
    """正向控制（L3）：adapter 归一后的 symbol 原样落 position_snapshot（.SHSE）。"""
    from unittest.mock import MagicMock, patch
    from src.strategy_runner import trading
    pos = NS(symbol="600036.SHSE", direction="direction_net", volume=800, frozen=0,
             avg_price=44.2, pnl=0.0, yd_volume=0)
    a = MagicMock()
    a.query_position.return_value = [pos]
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = conn.cursor.return_value
    cur.__enter__.return_value = cur
    with patch("src.data_platform.db.get_conn", return_value=conn):
        trading._flush_positions(a, 1, "tid")
    rows = cur.executemany.call_args.args[1]
    assert rows[0][1] == "600036.SHSE"
