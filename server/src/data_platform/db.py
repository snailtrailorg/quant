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

_dotenv_loaded = False
if not _dotenv_loaded:
    load_dotenv()  # 读 .env
    _dotenv_loaded = True


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

# 已建表的 freq 集合，避免每次 save_bars/get_bars 重复 DDL
_ensured_tables: set = set()


def get_conn() -> psycopg.Connection:
    """从连接池获取 psycopg 连接（with 退出还池，保留 psycopg 裸 SQL 风格）。"""
    return _engine.raw_connection()


def get_engine():
    """返回 SQLAlchemy engine（alembic/pandas 等用）。"""
    return _engine


def ensure_table(freq: str) -> None:
    """确保 K 线表存在，自动建表。"""
    if freq in _ensured_tables:
        return
    ddl = BAR_TABLE_DDL.format(freq=freq)
    with get_conn() as conn:
        conn.execute(ddl)
        conn.commit()
    _ensured_tables.add(freq)


def validate_bars(rows: list[tuple]) -> list[tuple]:
    """入库前校验（A2 #30）：剔 open/high/low/close=0 的行 + 标 ts 断点 warning（不剔）。

    Args:
        rows: 11 字段元组 (symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)
    Returns:
        清洗后 rows（剔 ohlc=0；ts 断点 per-symbol 相邻 >7 天记 warning，不剔）
    """
    import logging
    from collections import defaultdict
    logger = logging.getLogger("data_platform")
    if not rows:
        return rows
    # 1. 剔 ohlc=0（坏数据）
    clean = [r for r in rows if not (r[3] == 0 or r[4] == 0 or r[5] == 0 or r[6] == 0)]
    removed = len(rows) - len(clean)
    if removed:
        logger.warning(f"validate_bars: 剔除 ohlc=0 的行 {removed} 条")
    if not clean:
        return clean
    # 2. 标 ts 断点（per-symbol 相邻 >7 天，记 warning 不剔）
    by_symbol: dict = defaultdict(list)
    for r in clean:
        by_symbol[r[0]].append(r)
    for sym, sym_rows in by_symbol.items():
        sym_rows.sort(key=lambda x: x[2])  # 按 ts 排序
        for i in range(1, len(sym_rows)):
            prev_ts, curr_ts = sym_rows[i - 1][2], sym_rows[i][2]
            if hasattr(prev_ts, "date") and hasattr(curr_ts, "date"):
                gap = (curr_ts.date() - prev_ts.date()).days
                if gap > 7:
                    logger.warning(f"validate_bars: {sym} ts 断点 {prev_ts}~{curr_ts} 间隔 {gap} 天")
    return clean


_VALID_FREQS = {'1min', '5min', '15min', '30min', '60min', '1d'}


def save_bars(freq: str, rows: list[tuple]) -> int:
    """批量写入 K 线，冲突跳过。返回写入行数。

    入库前校验：validate_bars 剔 ohlc=0 + 标 ts 断点（A2 #30）。
    """
    if not rows:
        return 0
    rows = validate_bars(rows)
    if not rows:
        return 0
    # 大小写不敏感（写路径历史用 "1D" 读路径混用 "1d"；表名 PG 折叠同表。
    # 2026-08-18 盲审 F2：a28a5fa 加 assert 后 "1D" 被拒，日线同步静默断 11 天——教训：校验收紧必须 grep 全部调用点）
    assert freq.lower() in _VALID_FREQS, f"非法 freq: {freq}"
    ensure_table(freq)
    insert_sql = BAR_TABLE_INSERT.format(freq=freq)  # freq 是内部值，安全
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(insert_sql, rows)
        conn.commit()
        return len(rows)


def save_bars_overwrite(freq: str, rows: list[tuple]) -> int:
    """批量写入 K 线，冲突覆盖（回补用，体现手动回补优先级高于增量）。

    2026-08-18 G 审对齐：与 save_bars 同款校验（大小写不敏感 freq + validate_bars）——
    覆盖路径此前零校验（垃圾 freq 直接建野表/脏行直入）。"""
    if not rows:
        return 0
    rows = validate_bars(rows)
    if not rows:
        return 0
    assert freq.lower() in _VALID_FREQS, f"非法 freq: {freq}"
    ensure_table(freq)
    insert_sql = BAR_TABLE_INSERT_OVERWRITE.format(freq=freq)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(insert_sql, rows)
        conn.commit()
        return len(rows)


def get_bars(symbol: str, freq: str, start, end) -> pd.DataFrame:
    """查询 K 线，返回 DataFrame。

    用 cursor.fetchall 替代 pd.read_sql 避免 pandas/psycopg 不兼容警告。
    """
    ensure_table(freq)
    select_sql = BAR_TABLE_SELECT.format(freq=freq)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(select_sql, (symbol, start, end))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rows, columns=cols)
        # 显式转换数值列为 float64（psycopg3 Decimal 可能存为 object dtype）
        _numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount', 'adj_factor']
        for _col in _numeric_cols:
            if _col in df.columns:
                df[_col] = pd.to_numeric(df[_col], errors='coerce')
        if "ts" in cols:
            df["ts"] = pd.to_datetime(df["ts"])
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
    """初始化交易日历表（表已在 migration 0001 创建，保留接口兼容，不再 DDL）。"""
    return


def verify_schema() -> list[str]:
    """启动时校验所有基础设施表是否存在。缺失则 log warning，不自动创建。"""
    import logging
    logger = logging.getLogger("data_platform")
    required_tables = [
        "users", "audit_log", "sync_config", "sync_log",
        "account_snapshot", "accounts", "alert_history", "astock_analysis",
        "broker_usage", "convertible_terms", "signal_log", "order_log",
        "trade_log", "static_symbols",
        "system_config", "llm_model_config", "llm_usage",
        "feishu_config", "live_trading_config", "live_task",
        "pools", "pool_symbols", "strategy_account",
        "broker_config", "risk_rules", "channel_config",
        "tasks", "task_logs", "factor_def",
        "backtest_runs", "backtest_symbols",
        "data_source_config", "data_source_usage",
        "llm_budget", "user_tokens",
    ]
    missing = []
    with get_conn() as conn:
        for table in required_tables:
            try:
                conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
            except Exception:
                missing.append(table)
    if missing:
        logger.warning(f"数据库缺少表，请运行 alembic upgrade head: {missing}")
    else:
        logger.info("数据库 schema 校验通过")
    return missing