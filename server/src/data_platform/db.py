"""数据中台 · PostgreSQL 数据库操作。

连接管理（SQLAlchemy 连接池）/ 表创建 / 写入 / 查询。
"""

from __future__ import annotations
import os
import pandas as pd
import psycopg
from sqlalchemy import create_engine
from dotenv import load_dotenv

from .schema import BAR_TABLE_INSERT, BAR_TABLE_INSERT_OVERWRITE, BAR_TABLE_SELECT, parse_vt_symbol

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
#
# DB 优化防线（2026-08-21，arch-18 文档 §1.7 + 锁链事件根治）：
# - statement_timeout：杀"SQL 本身跑太久"（默认 60s；web 进程由 systemd 环境覆盖 10s）
# - idle_in_transaction_session_timeout：杀"事务开着不干活"（psycopg 非 autocommit +
#   SELECT 不 commit 的读块在全仓普遍存在——服务端兜底比逐点修治本）
# - lock_timeout：杀"等锁闷死"（10s——alembic DDL 被长事务堵时快速失败而非无限排队）
# 注意：statement_timeout 杀不住"等网络/锁"的会话——那由 idle_in_tx/lock_timeout 各管一段。
def _db_session_options() -> dict:
    import os as _os
    stmt_ms = int(_os.environ.get("QUANT_DB_STMT_TIMEOUT_MS", "60000"))
    idle_ms = int(_os.environ.get("QUANT_DB_IDLE_TX_TIMEOUT_MS", "300000"))
    lock_ms = int(_os.environ.get("QUANT_DB_LOCK_TIMEOUT_MS", "10000"))
    return {"options": f"-c statement_timeout={stmt_ms} "
                       f"-c idle_in_transaction_session_timeout={idle_ms} "
                       f"-c lock_timeout={lock_ms}"}


_engine = create_engine(
    get_conn_url().replace("postgresql://", "postgresql+psycopg://", 1),
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,  # 30 分钟回收，避免长连接超时
    connect_args=_db_session_options(),
)

# 已校验存在的 freq 集合，避免热路径重复 to_regclass 查询（建表由 alembic 迁移负责）
_verified_tables: set = set()


def get_conn() -> psycopg.Connection:
    """从连接池获取 psycopg 连接（with 退出还池，保留 psycopg 裸 SQL 风格）。"""
    return _engine.raw_connection()


def get_engine():
    """返回 SQLAlchemy engine（alembic/pandas 等用）。"""
    return _engine


def ensure_table(freq: str) -> None:
    """校验 K 线表存在（建表由 alembic 迁移负责，运行时不再 CREATE TABLE IF NOT EXISTS）。

    2026-09-03：bar_{freq} 8 表已全部入迁移（0064 补齐 15min/30min/60min/1h/4h）。
    运行时只校验 + 告警——表不存在（迁移未跑）告警，后续 INSERT/SELECT 由 PG 报
    relation does not exist。首次校验成功缓存（键用 lower 后表名，"1H"/"1h" 同表不重复），
    避免热路径重复 to_regclass 查询。
    """
    import logging
    logger = logging.getLogger("data_platform")
    table = f"bar_{freq.lower()}"
    if table in _verified_tables:
        return
    with get_conn() as conn:
        cur = conn.execute("SELECT to_regclass(%s)", (table,))
        if cur.fetchone()[0] is None:
            logger.warning("%s 表不存在（alembic upgrade head 建表，勿运行时 CREATE TABLE）", table)
            return   # 不缓存：下次再校验（迁移可能已跑）
    _verified_tables.add(table)


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


# P2 修复（2026-08-20 双盲审计）：与 schema.Freq Literal（1H/4H/1D）及历史小写口径并存的
# 超集白名单——原两套互斥，按 Literal 传 "1H" 会被 assert 拒（2026-08-18 "1D 被拒断 11 天"同族坑）
_VALID_FREQS = {'1min', '5min', '15min', '30min', '60min', '1h', '4h', '1d',
                '1H', '4H', '1D'}


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


def refresh_minute_symbols() -> int:
    """重算 minute_symbols 展开表的 pool 部分（幂等；direct 行不动）。

    池级标记（pools.minute_history_start 非空的 astock 池成员）→ source='pool:{id}'；
    个股直标 source='direct' 由 API 直接 UPSERT，不参与重算。direct 优先：同标的既直标
    又在池时，INSERT ON CONFLICT DO NOTHING 保留已存在的 direct 行（盲审 A-P0/B-P1 同根）。
    返回展开表总行数。minute_symbols 由迁移 0066 建表。
    """
    with get_conn() as conn:
        conn.execute("DELETE FROM minute_symbols WHERE source LIKE 'pool:%'")
        conn.execute(
            "INSERT INTO minute_symbols (symbol, source) "
            "SELECT DISTINCT ON (ps.symbol) ps.symbol, 'pool:' || p.id "
            "FROM pool_symbols ps JOIN pools p ON p.id = ps.pool_id "
            "WHERE p.minute_history_start IS NOT NULL AND p.category = 'astock' "
            "AND ps.symbol NOT LIKE '%.BSE' "   # 北交所腾讯 mkline 不支持（盲审 A-P2/B-P2）
            "ON CONFLICT (symbol) DO NOTHING")
        conn.commit()
        cur = conn.execute("SELECT count(*) FROM minute_symbols")
        return cur.fetchone()[0]


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


def save_index_bars(rows: list[tuple]) -> int:
    """批量写入指数日线到 bar_index 表（回测基准数据）。冲突跳过，返回**实际插入行数**。

    rows 11 字段同 save_bars：(symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)。
    """
    if not rows:
        return 0
    insert_sql = (
        "INSERT INTO bar_index (symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (symbol, ts) DO NOTHING"
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(insert_sql, rows)
        conn.commit()
        rc = cur.rowcount
        return rc if isinstance(rc, int) and rc > 0 else 0   # 幂等重同步时真实插入数（非 len(rows)）


def get_index_bars(symbol: str, start, end) -> pd.DataFrame:
    """查询指数日线（bar_index），返回 DataFrame（symbol 为 vt_symbol 如 000300.SHSE）。

    与 get_bars 同款列转换（数值 float64 + ts datetime）。
    """
    cols = ["ts", "open", "high", "low", "close", "volume", "amount"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ts, open, high, low, close, volume, amount FROM bar_index "
                "WHERE symbol=%s AND ts >= %s AND ts <= %s ORDER BY ts",
                (symbol, start, end))
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rows, columns=cols)
        for _col in ("open", "high", "low", "close", "volume", "amount"):
            df[_col] = pd.to_numeric(df[_col], errors='coerce')
        df["ts"] = pd.to_datetime(df["ts"])
        return df


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


def load_schema_expectations() -> dict[str, set[str]]:
    """加载链生成的期望清单（schema_expectations.txt，85 表，随迁移增长）。

    生成方式（#48 L-S-A 生成式，禁手写）：
      PGPASSWORD=… psql -d quant -q -c "DROP SCHEMA IF EXISTS chain_scratch CASCADE; CREATE SCHEMA chain_scratch;"
      QUANT_DB_URL=… PGOPTIONS="-c search_path=chain_scratch" python -m alembic upgrade head
      psql -At -c "SELECT table_name||' :: '||string_agg(column_name, ',' ORDER BY ordinal_position)
                   FROM information_schema.columns WHERE table_schema='chain_scratch'
                   GROUP BY table_name ORDER BY table_name;" > src/data_platform/schema_expectations.txt
      （用完 DROP SCHEMA chain_scratch CASCADE）——每加迁移重跑并提交。
    """
    import os
    exp: dict[str, set[str]] = {}
    path = os.path.join(os.path.dirname(__file__), "schema_expectations.txt")
    try:
        with open(path) as f:
            for line in f:
                if " :: " in line:
                    t, cols = line.strip().split(" :: ", 1)
                    exp[t] = set(c.strip() for c in cols.split(","))
    except FileNotFoundError:
        pass
    return exp


def verify_schema() -> dict[str, list[str]]:
    """列级校验（#48 v2，L 审修正案）：expected ⊆ actual 单向存在性比对。

    - 期望来源=迁移链生成的 schema_expectations.txt（非手写清单——手写必腐，仓内已有实锤）
    - 单条 information_schema 查询拿全部列（非逐表探测）
    - **纯函数返回 findings，不做告警**——db.py 是最底层模块，告警路由归入口层
      （web startup / runner / hub / celery 父进程，复用 monitor 容错范式），避免依赖倒置
    - 只比缺列（0038 类问题）；不比 data_type/多余列（链外遗留不阻断）
    """
    import logging
    logger = logging.getLogger("data_platform")
    expected = load_schema_expectations()
    if not expected:
        # M-S1：文件缺失=校验被静默禁用，本身必须可见——哨兵交给入口层路由
        logger.warning("schema_expectations.txt 缺失或为空，列级校验被禁用（哨兵上报）")
        return {"missing_tables": [], "missing_columns": {}, "expectations_missing": True}
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name = ANY(%s)",
            (list(expected.keys()),))
        actual: dict[str, set[str]] = {}
        for t, c in cur.fetchall():
            actual.setdefault(t, set()).add(c)
    missing_tables = sorted(set(expected) - set(actual))
    missing_columns = {t: sorted(expected[t] - actual.get(t, set()))
                       for t in expected if t in actual and expected[t] - actual[t]}
    if missing_tables or missing_columns:
        logger.warning("schema 校验发现缺失（alembic upgrade head 或查迁移链）: 表%s 列%s",
                       missing_tables, missing_columns)
    else:
        logger.info("数据库 schema 列级校验通过（%d 表）", len(expected))
    return {"missing_tables": missing_tables, "missing_columns": missing_columns}