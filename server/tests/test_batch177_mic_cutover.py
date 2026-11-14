"""批177 真库守门：任何含 symbol 语义的列都不得残留旧后缀。

背景：0183 靠手写表清单，漏了 position_snapshot（上产后 /api/position 仍是 .SHSE）。
本守门改为**扫 information_schema 全库**，新增 symbol 列自动纳入，防再漏。
"""
import pytest

_OLD_SUFFIXES = (".SHSE", ".SZSE", ".BSE", ".BINANCE", ".OKX")


def _count_old(conn, table: str, column: str) -> int:
    cond = " OR ".join(f"{column} LIKE '%{s}'" for s in _OLD_SUFFIXES)
    return conn.execute(f"SELECT count(*) FROM {table} WHERE {cond}").fetchone()[0]


@pytest.mark.parametrize("table,column", [
    ("position_snapshot", "symbol"), ("bar_1d", "symbol"), ("security_master", "vt_symbol"),
])
def test_known_tables_mic(table, column):
    from src.data_platform.db import get_conn
    with get_conn() as c:
        n = _count_old(c, table, column)
    assert n == 0, f"{table}.{column} 仍有 {n} 行旧后缀"


def test_all_symbol_columns_are_mic():
    """全库扫描：所有 symbol / vt_symbol 列零旧后缀（新表自动纳入）。"""
    from src.data_platform.db import get_conn
    bad = []
    with get_conn() as c:
        rows = c.execute("""
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema='public' AND column_name IN ('symbol', 'vt_symbol')
            ORDER BY 1, 2""").fetchall()
        for table, column in rows:
            n = _count_old(c, table, column)
            if n:
                bad.append(f"{table}.{column}={n}")
    assert not bad, "旧后缀残留：" + ", ".join(bad)
