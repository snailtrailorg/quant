"""批178 守门：派生指标 inf/nan 不得进 JSON 响应（FastAPI allow_nan=False → 500）。"""
import math
from unittest.mock import MagicMock, patch

from src.data_platform import screen as SC


def test_run_query_sanitizes_non_finite():
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.description = [("ts_code",), ("premium_pct",), ("double_low",)]
    cur.fetchall.return_value = [("113056.SH", float("inf"), float("nan"))]
    conn.execute.return_value = cur
    with patch("src.data_platform.db.get_conn", return_value=conn), \
         patch.object(SC, "build_base_from", return_value="SELECT 1 AS ts_code"):
        out = SC.run_query("convertible", [], "", [], 10, [])
    row = out["rows"][0]
    assert row["premium_pct"] is None and row["double_low"] is None
    assert row["ts_code"] == "113056.SH" and row["vt_symbol"] == "113056.XSHG"


def test_screen_cb_api_sanitizes_non_finite():
    from src.web_api.routes import stock as R
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.fetchall.return_value = [("113056.SH", "重银转债", "601963.SH", "重庆银行", 9.22,
                                  "20280323", 123.4, 12.1, float("inf"), float("nan"))]
    conn.execute.return_value = cur
    with patch.object(R, "get_conn", return_value=conn):
        out = R.screen_cb_api(payload={})
    assert out[0]["double_low"] is None and out[0]["premium_pct"] is None

def test_run_query_sanitizes_decimal_infinity():
    """numeric 列是 Decimal：Decimal('Infinity') 也必须降级 None。"""
    from decimal import Decimal
    from unittest.mock import MagicMock, patch
    from src.data_platform import screen as SC

    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = MagicMock()
    cur.description = [("ts_code",), ("premium_pct",)]
    cur.fetchall.return_value = [("113056.SH", Decimal("Infinity")), ("127061.SZ", Decimal("NaN"))]
    conn.execute.return_value = cur
    with patch("src.data_platform.db.get_conn", return_value=conn), \
         patch.object(SC, "build_base_from", return_value="SELECT 1 AS ts_code"):
        out = SC.run_query("convertible", [], "", [], 10, [])
    assert all(r["premium_pct"] is None for r in out["rows"])
