"""数据中台 · 统一 K 线 schema 与 vt_symbol 工具函数。

回测与实盘 schema 一致（对齐 XTP 实时行情），零迁移。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

Freq = Literal["1min", "5min", "15min", "1H", "4H", "1D"]

# Tushare 交易所 → vnpy Exchange 枚举映射
TS_EXCHANGE_MAP = {
    "SH": "SHSE",
    "SZ": "SZSE",
    "BJ": "BSE",
    "SSE": "SHSE",  # Tushare 有时也用 SSE
    "SZE": "SZSE",
}

# 反向映射（vt_symbol → Tushare ts_code）
EXCHANGE_TS_MAP = {v: k for k, v in TS_EXCHANGE_MAP.items() if k in ("SH", "SZ")}
# 补充
EXCHANGE_TS_MAP["SHSE"] = "SH"
EXCHANGE_TS_MAP["SZSE"] = "SZ"
EXCHANGE_TS_MAP["BSE"] = "BJ"
# 批 137a：`.SSE` 是本仓合法形态（fees.py:33 / risk.py:340 / tasks.py:241 实证快照即 600000.SSE）——
# 缺这一项时 vt_to_ts("600000.SSE") 原样返回 .SSE，发给 tushare 必失败。
EXCHANGE_TS_MAP["SSE"] = "SH"


def vt_to_ts(vt_symbol: str) -> str:
    """vnpy vt_symbol → Tushare ts_code（**薄别名**，实现唯一＝to_ts_code）。

    批 137a：保留原名是为了不动 5 处调用点（stock_detail/perms/backtest/sync/pool_data）；
    两个同义函数各写一份实现，正是 .SSE 映射缺失被复制成两处的成因。

    >>> vt_to_ts("600000.SHSE")
    "600000.SH"
    """
    return to_ts_code(vt_symbol)


def to_vt_symbol(ts_code: str) -> str:
    """Tushare ts_code → vnpy vt_symbol。

    >>> to_vt_symbol("600000.SH")
    "600000.SHSE"
    >>> to_vt_symbol("000001.SZ")
    "000001.SZSE"
    """
    if "." not in ts_code:
        return ts_code
    sym, ex = ts_code.rsplit(".", 1)
    ex_up = ex.upper()
    mapped = TS_EXCHANGE_MAP.get(ex_up, ex_up)
    return f"{sym}.{mapped}"


def to_ts_code(vt_symbol: str) -> str:
    """vnpy vt_symbol → Tushare ts_code。

    >>> to_ts_code("600000.SHSE")
    "600000.SH"
    """
    if "." not in vt_symbol:
        return vt_symbol
    sym, ex = vt_symbol.rsplit(".", 1)
    ex_up = ex.upper()
    mapped = EXCHANGE_TS_MAP.get(ex_up, ex_up)
    return f"{sym}.{mapped}"


def parse_vt_symbol(vt_symbol: str) -> tuple[str, str]:
    """vt_symbol → (raw_symbol, exchange)。

    >>> parse_vt_symbol("600000.SHSE")
    ("600000", "SHSE")
    """
    if "." not in vt_symbol:
        return vt_symbol, ""
    sym, ex = vt_symbol.rsplit(".", 1)
    return sym, ex.upper()


@dataclass
class Bar:
    """统一 K 线数据结构，与 PostgreSQL bar 表对齐。"""
    symbol: str        # vt_symbol: 600000.SHSE
    freq: str          # 1D / 1H / 1min …
    ts: datetime       # 行情时间戳，A 股 +08:00，加密 UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal    # 成交额（元）
    adj_factor: Decimal | None = None   # 复权因子
    source: str = "tushare"             # 数据源标识

BAR_TABLE_INSERT = """
INSERT INTO bar_{freq} (symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (symbol, ts) DO NOTHING
"""

# 覆盖版：回补时本地已有数据也覆盖（体现手动回补优先级高于增量）
BAR_TABLE_INSERT_OVERWRITE = """
INSERT INTO bar_{freq} (symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (symbol, ts) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
    volume = EXCLUDED.volume, amount = EXCLUDED.amount,
    adj_factor = COALESCE(EXCLUDED.adj_factor, bar_{freq}.adj_factor),   -- F-F2：回补覆盖不得把已回填因子清回 NULL
    source = EXCLUDED.source
"""

BAR_TABLE_SELECT = """
SELECT symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source
FROM bar_{freq}
WHERE symbol = %s AND ts >= %s AND ts <= %s
ORDER BY ts ASC
"""
