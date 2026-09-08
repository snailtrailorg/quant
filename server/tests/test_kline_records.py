"""批10：get_kline_records 零 pandas 直查的转换单测。

覆盖：Decimal→float、None→None、Decimal('NaN')→None（防非法 JSON NaN 字面量——
现状 DataFrame 路径 pd.notna 语义对齐）、aware datetime→strftime('%Y-%m-%d')、
freq 白名单 assert（web 层公开可达入口的注入面，对齐 save_bars 写路径标准）。
"""
import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from src.data_platform import db


def _mock_conn(g, rows):
    """装配 with get_conn() as conn / with conn.cursor() as cur 两层协议链。"""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.description = None
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    g.return_value.__enter__.return_value = conn
    return cur


def _mock_rows():
    ts = datetime(2026, 9, 5, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    return [
        # 正常行：Decimal（psycopg3 numeric）→ float
        (ts, Decimal("10.5"), Decimal("11.0"), Decimal("10.2"), Decimal("10.8"), Decimal("1234567")),
        # 边缘行：Decimal('NaN') 与 None 混合
        (ts, Decimal("NaN"), None, Decimal("10.0"), Decimal("10.0"), Decimal("0")),
    ]


def test_kline_records_conversion():
    with patch.object(db, "ensure_table"), patch.object(db, "get_conn") as g:
        cur = _mock_conn(g, _mock_rows())
        out = db.get_kline_records("600000.SHSE", "1D", None, None)
        # SQL 守门（代码盲审 A/B 同判）：列序与 r[0..5] 位置索引硬耦合，断言全文防误删/调换。
        # bar_1D 保持 freq 原样（PG 标识符折叠命中 bar_1d，与 get_bars/BAR_TABLE_SELECT 同待遇）
        sql, params = cur.execute.call_args[0]
        assert sql == ("SELECT ts, open, high, low, close, volume FROM bar_1D "
                       "WHERE symbol=%s AND ts >= %s AND ts <= %s ORDER BY ts ASC")
        assert params == ("600000.SHSE", None, None)
    assert out[0] == {"ts": "2026-09-05", "open": 10.5, "high": 11.0,
                      "low": 10.2, "close": 10.8, "volume": 1234567.0}
    assert out[1]["open"] is None    # Decimal('NaN') → None（非法 JSON 防御）
    assert out[1]["high"] is None    # None → None
    assert out[1]["low"] == 10.0
    assert out[1]["volume"] == 0.0


def test_kline_records_empty():
    with patch.object(db, "ensure_table"), patch.object(db, "get_conn") as g:
        _mock_conn(g, [])
        assert db.get_kline_records("600000.SHSE", "1D", None, None) == []


def test_kline_records_freq_guard():
    """非法 freq 在 ensure_table 前被 _VALID_FREQS assert 拦截（不构成任何 SQL 执行）。"""
    with patch.object(db, "ensure_table") as et, patch.object(db, "get_conn") as g:
        with pytest.raises(AssertionError, match="非法 freq"):
            db.get_kline_records("X", "1d; DROP TABLE bar_1d", None, None)
        et.assert_not_called()
        g.assert_not_called()
