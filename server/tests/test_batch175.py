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


def test_flush_positions_normalizes_sse_and_skips_bare():
    """批175e：写入口兜底——.SSE 归一为 .SHSE；真裸码（无交易所）跳过并留 error。"""
    from unittest.mock import MagicMock, patch
    from src.strategy_runner import trading
    a = MagicMock()
    a.query_position.return_value = [
        NS(symbol="600036.SSE", direction="direction_net", volume=800, frozen=0,
           avg_price=44.2, pnl=0.0, yd_volume=0),
        NS(symbol="600000", direction="direction_net", volume=100, frozen=0,
           avg_price=8.0, pnl=0.0, yd_volume=0),
    ]
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = conn.cursor.return_value
    cur.__enter__.return_value = cur
    with patch("src.data_platform.db.get_conn", return_value=conn):
        trading._flush_positions(a, 1, "tid")
    rows = cur.executemany.call_args.args[1]
    assert len(rows) == 1 and rows[0][1] == "600036.SHSE"


def test_legacy_position_normalizes_vnpy_shape():
    """批175e：legacy 路径 vnpy PositionData 是裸码 + exchange 枚举值 → 必须归一到 vt。"""
    from src.strategy_runner.legacy_translate import to_domain_position
    p = NS(symbol="600036", exchange=NS(value="SSE"), volume=100, avg_price=1.0,
           pnl=0.0, frozen=0, yd_volume=0)
    assert to_domain_position(p).symbol == "600036.SHSE"
    q = NS(symbol="000001", exchange=NS(value="SZSE"), volume=100, avg_price=1.0,
           pnl=0.0, frozen=0, yd_volume=0)
    assert to_domain_position(q).symbol == "000001.SZSE"
    r = NS(symbol="600036.SHSE", volume=100, avg_price=1.0, pnl=0.0, frozen=0, yd_volume=0)
    assert to_domain_position(r).symbol == "600036.SHSE"      # 已是 vt → 幂等


def test_orders_read_side_normalizes_sse():
    """批175e：/api/orders 读侧 SQL 必须归一一（order_log 历史 .SSE 不改写、出参 canonical）。"""
    from datetime import datetime
    from unittest.mock import MagicMock, patch
    from src.web_api.routes import trading as routes

    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [
        (datetime(2026, 11, 13), "manual", "600036.SSE", "BUY", 100, 44.2, "all_traded", "mo-1", None)]
    with patch.object(routes, "get_conn", return_value=conn):
        out = routes.get_orders(payload={})
    sql = conn.execute.call_args.args[0]
    assert "replace(symbol, '.SSE', '.SHSE')" in sql, "读侧未归一——委托列表会继续透出 .SSE"
    assert out["orders"][0]["symbol"] == "600036.SSE"      # 归一身 SQL 侧（mock 不执行）


def test_instrument_spec_exposes_mic_code():
    """批175e：mic_code 必须有读出口（原只写不读＝死列）。"""
    from decimal import Decimal
    from unittest.mock import MagicMock, patch
    from src.data_platform import specs as SP

    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = (
        "600036.SHSE", "astock", "SHSE", "stock", None, 100, 0.01,
        "continuous", "volume_minus_frozen", ["limit"], "long_only", {}, None, "XSHG")
    with patch.object(SP, "get_conn", return_value=conn):
        spec = SP._load_instrument("600036.SHSE")
    assert spec.mic_code == "XSHG"
    assert spec.lot_size == 100
