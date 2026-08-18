"""数据中台 · 统一 K 线 schema 与 vt_symbol 工具函数。

回测与实盘 schema 一致（对齐 XTP 实时行情），零迁移。
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
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

    @classmethod
    def from_tushare_row(cls, row: dict, symbol: str | None = None) -> "Bar":
        """从 Tushare 日线 dict 构建 Bar。"""
        import pandas as pd
        ts = pd.Timestamp(row.get("trade_date", row.get("ts"))).to_pydatetime()
        return cls(
            symbol=symbol or to_vt_symbol(row.get("ts_code", "")),
            freq="1D",
            ts=ts,
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=Decimal(str(row.get("vol", 0))),
            amount=Decimal(str(row.get("amount", 0))),
            adj_factor=Decimal(str(row["adj_factor"])) if row.get("adj_factor") is not None else None,
            source="tushare",
        )


# ——— K 线表 DDL ———

BAR_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS bar_{freq} (
    id        BIGSERIAL PRIMARY KEY,
    symbol    TEXT NOT NULL,
    freq      TEXT NOT NULL DEFAULT '{freq}',
    ts        TIMESTAMPTZ NOT NULL,
    open      NUMERIC NOT NULL,
    high      NUMERIC NOT NULL,
    low       NUMERIC NOT NULL,
    close     NUMERIC NOT NULL,
    volume    NUMERIC NOT NULL DEFAULT 0,
    amount    NUMERIC NOT NULL DEFAULT 0,
    adj_factor NUMERIC,
    source    TEXT NOT NULL DEFAULT 'tushare',
    UNIQUE(symbol, ts)
);
CREATE INDEX IF NOT EXISTS idx_bar_{freq}_symbol_ts ON bar_{freq} (symbol, ts DESC);
"""

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