"""数据中台 · PostgreSQL 数据库操作。

连接管理（SQLAlchemy 连接池）/ 表创建 / 写入 / 查询。
"""

from __future__ import annotations
import os
import pandas as pd
import psycopg
from sqlalchemy import create_engine
from dotenv import load_dotenv

from .schema import BAR_TABLE_DDL, BAR_TABLE_INSERT, BAR_TABLE_INSERT_OVERWRITE, BAR_TABLE_SELECT, parse_vt_symbol

load_dotenv()  # 读 .env


def get_conn_url() -> str:
    """从环境变量获取数据库连接串。"""
    return os.environ.get(
        "QUANT_DB_URL",
        "postgresql://quant@127.0.0.1:5432/quant",
    )


# SQLAlchemy 连接池（pool_size=10 + max_overflow=20，pre_ping 自动检测断连）
# URL 转 postgresql+psycopg:// 给 SQLAlchemy（psycopg3 驱动）
_engine = create_engine(
    get_conn_url().replace("postgresql://", "postgresql+psycopg://", 1),
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,  # 30 分钟回收，避免长连接超时
)


def get_conn() -> psycopg.Connection:
    """从连接池获取 psycopg 连接（with 退出还池，保留 psycopg 裸 SQL 风格）。"""
    return _engine.raw_connection()


def get_engine():
    """返回 SQLAlchemy engine（alembic/pandas 等用）。"""
    return _engine


def ensure_table(freq: str) -> None:
    """确保 K 线表存在，自动建表。"""
    ddl = BAR_TABLE_DDL.format(freq=freq)
    with get_conn() as conn:
        conn.execute(ddl)
        conn.commit()


def save_bars(freq: str, rows: list[tuple]) -> int:
    """批量写入 K 线，冲突跳过。返回写入行数。"""
    if not rows:
        return 0
    ensure_table(freq)
    insert_sql = BAR_TABLE_INSERT.format(freq=freq)  # freq 是内部值，安全
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(insert_sql, rows)
        conn.commit()
        return len(rows)


def save_bars_overwrite(freq: str, rows: list[tuple]) -> int:
    """批量写入 K 线，冲突覆盖（回补用，体现手动回补优先级高于增量）。"""
    if not rows:
        return 0
    ensure_table(freq)
    insert_sql = BAR_TABLE_INSERT_OVERWRITE.format(freq=freq)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(insert_sql, rows)
        conn.commit()
        return len(rows)


def get_bars(symbol: str, freq: str, start, end) -> pd.DataFrame:
    """查询 K 线，返回 DataFrame。"""
    ensure_table(freq)
    select_sql = BAR_TABLE_SELECT.format(freq=freq)
    with get_conn() as conn:
        df = pd.read_sql_query(
            select_sql,
            conn,
            params=(symbol, start, end),
            parse_dates=["ts"],
        )
        return df


def get_trade_calendar(year: int) -> list[date]:
    """从数据库取 A 股交易日历（来源 Tushare trade_cal）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT cal_date FROM trade_cal WHERE exchange='SSE' AND is_open=1 AND cal_date >= %s AND cal_date < %s ORDER BY cal_date",
            (f"{year}0101", f"{year+1}0101"),
        ).fetchall()
        if rows:
            from datetime import date
            return [r[0] for r in rows]
    return []


def is_trading_day(d: date | None = None) -> bool:
    """判断某天是否为 A 股交易日。"""
    from datetime import date
    d = d or date.today()
    cal = get_trade_calendar(d.year)
    return d in cal


def init_trade_calendar(year: int) -> None:
    """初始化交易日历表（需已从 Tushare 拉取数据存入）。"""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_cal (
                exchange TEXT, cal_date DATE, is_open INT, pretrade_date DATE,
                PRIMARY KEY(exchange, cal_date)
            )
        """)
        conn.commit()