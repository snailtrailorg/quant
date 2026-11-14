"""批177：内部 canonical 全量切 ISO 10383 MIC（单次原子迁移）。

映射（等长/变长混合替换）：
  .SHSE→.XSHG  .SZSE→.XSHE  .BSE→.BJSE  .BINANCE→.XBIN  .OKX→.XOKX
crypto 的 XBIN/XOKX 是**自定义 venue 码**（ISO 10383 未占用，已核官方 CSV）。

覆盖：全部平台内部 symbol 列（行情/状态/委托/风控/池/回测/配置/派生缓存）+ security_master
（vt_symbol / exchange / mic_code）+ account_permission.allowed_exchanges + reconcile_issue（裸码合并回填）。

顺序理由：先转 security_master（供 reconcile 反查），再转其余表，最后断言零残留。
downgrade 反向替换（与代码回滚同批执行）；crypto 反向同样可逆（.XBIN→.BINANCE）。

Revision ID: 0183
Revises: 0182
"""
from alembic import op

revision = "0183"
down_revision = "0182"

_OLD_RE = r"\.(SHSE|SZSE|BSE|BINANCE|OKX)$"

# (表, 列)；不存在/空表的 UPDATE 是 no-op
_SYMBOL_COLS = [
    ("bar_1min", "symbol"), ("bar_5min", "symbol"), ("bar_15min", "symbol"),
    ("bar_30min", "symbol"), ("bar_1h", "symbol"), ("bar_4h", "symbol"),
    ("bar_60min", "symbol"), ("bar_1d", "symbol"), ("bar_index", "symbol"),
    ("security_state", "vt_symbol"), ("daily_basic", "vt_symbol"),
    ("risk_log", "symbol"), ("broker_usage", "symbol"), ("sync_gap", "symbol"),
    ("strategy_config", "symbol"), ("freeze_event", "symbol"),
    ("backtest_symbols", "symbol"), ("pool_symbols", "symbol"),
    ("order_log", "symbol"), ("trade_log", "symbol"), ("paper_trade_log", "symbol"),
    ("signal_log", "symbol"), ("live_task", "symbol"), ("hub_transient_subs", "symbol"),
    ("astock_analysis", "symbol"),
]


def _chain(col: str, reverse: bool = False) -> str:
    if reverse:
        return ("replace(replace(replace(replace(replace({c}, '.XSHG','.SHSE'), '.XSHE','.SZSE'), "
                "'.BJSE','.BSE'), '.XBIN','.BINANCE'), '.XOKX','.OKX')").format(c=col)
    return ("replace(replace(replace(replace(replace({c}, '.SHSE','.XSHG'), '.SZSE','.XSHE'), "
            "'.BSE','.BJSE'), '.BINANCE','.XBIN'), '.OKX','.XOKX')").format(c=col)


def upgrade() -> None:
    # 1) security_master：主键 + 交易所列 + MIC 列
    op.execute(f"UPDATE security_master SET vt_symbol = {_chain('vt_symbol')} "
               f"WHERE vt_symbol ~ '{_OLD_RE}'")
    op.execute(
        "UPDATE security_master SET exchange = CASE exchange "
        "  WHEN 'SHSE' THEN 'XSHG' WHEN 'SZSE' THEN 'XSHE' WHEN 'BSE' THEN 'BJSE' "
        "  WHEN 'BINANCE' THEN 'XBIN' WHEN 'OKX' THEN 'XOKX' ELSE exchange END"
    )
    op.execute("UPDATE security_master SET mic_code = split_part(vt_symbol, '.', 2)")

    # 2) 其余内部 symbol 列
    for table, col in _SYMBOL_COLS:
        op.execute(f"UPDATE {table} SET {col} = {_chain(col)} WHERE {col} ~ '{_OLD_RE}'")

    # 3) 因子缓存：加 vt 键列并回填 MIC（ts_code 保留，contract 另批删）
    op.execute("ALTER TABLE screen_factor_cache ADD COLUMN IF NOT EXISTS vt_symbol TEXT")
    op.execute(
        "UPDATE screen_factor_cache SET vt_symbol = "
        "split_part(ts_code, '.', 1) || CASE split_part(ts_code, '.', 2) "
        "  WHEN 'SH' THEN '.XSHG' WHEN 'SZ' THEN '.XSHE' WHEN 'BJ' THEN '.BJSE' "
        "  ELSE '.' || split_part(ts_code, '.', 2) END"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_sfc_vt ON screen_factor_cache "
               "(vt_symbol, category, factor, trade_date)")

    # 4) 账户权限里的交易所枚举（text[]）
    op.execute(
        "UPDATE account_permission SET allowed_exchanges = ARRAY("
        "  SELECT CASE x WHEN 'SHSE' THEN 'XSHG' WHEN 'SZSE' THEN 'XSHE' WHEN 'BSE' THEN 'BJSE' "
        "              WHEN 'BINANCE' THEN 'XBIN' WHEN 'OKX' THEN 'XOKX' ELSE x END "
        "  FROM unnest(allowed_exchanges) AS x) "
        "WHERE allowed_exchanges && ARRAY['SHSE','SZSE','BSE','BINANCE','OKX']"
    )

    # 5) 对账差异单：先合并 vt 孪生 open 行，再裸码 → MIC（尾段前缀兜底）
    op.execute("""
        DELETE FROM reconcile_issue b
        USING (
            SELECT r.id FROM reconcile_issue r
            JOIN security_master m
              ON m.category IN ('stock', 'listed_fund', 'convertible')
             AND split_part(m.vt_symbol, '.', 1) = r.symbol
            WHERE r.symbol ~ '^[0-9]{6}$' AND r.status = 'open'
              AND EXISTS (SELECT 1 FROM reconcile_issue v
                          WHERE v.symbol = m.vt_symbol AND v.issue_type = r.issue_type
                            AND v.status = 'open')
        ) dup
        WHERE b.id = dup.id
    """)
    op.execute("""
        UPDATE reconcile_issue r SET symbol = m.vt_symbol, updated_at = now()
        FROM security_master m
        WHERE r.symbol ~ '^[0-9]{6}$'
          AND m.category IN ('stock', 'listed_fund', 'convertible')
          AND split_part(m.vt_symbol, '.', 1) = r.symbol
    """)
    # 主档查不到的（退市/未回填）按代码段兜底，避免留裸码
    op.execute("""
        UPDATE reconcile_issue SET symbol = CASE
            WHEN symbol LIKE '6%' OR symbol LIKE '5%' OR symbol LIKE '11%' THEN symbol || '.XSHG'
            WHEN symbol LIKE '12%' OR symbol LIKE '15%' OR symbol LIKE '16%' OR symbol LIKE '18%'
                 OR symbol LIKE '0%' OR symbol LIKE '3%' THEN symbol || '.XSHE'
            WHEN symbol LIKE '4%' OR symbol LIKE '8%' OR symbol LIKE '9%' THEN symbol || '.BJSE'
            ELSE symbol END
        WHERE symbol ~ '^[0-9]{6}$'
    """)

    # 6) 尾断：任何表残留旧后缀 / 差异单残留裸码 → abort（同 0182 前置断言风格）
    leftovers = []
    for table, col in [("security_master", "vt_symbol")] + _SYMBOL_COLS:
        n = op.get_bind().exec_driver_sql(
            f"SELECT count(*) FROM {table} WHERE {col} ~ '{_OLD_RE}'").scalar()
        if n:
            leftovers.append(f"{table}.{col}={n}")
    bare = op.get_bind().exec_driver_sql(
        "SELECT count(*) FROM reconcile_issue WHERE symbol ~ '^[0-9]{6}$'").scalar()
    if bare:
        leftovers.append(f"reconcile_issue.bare={bare}")
    if leftovers:
        raise RuntimeError("0183 迁移后仍有旧口径残留：" + ", ".join(leftovers))


def downgrade() -> None:
    op.execute("UPDATE reconcile_issue SET symbol = replace(replace(replace(replace(replace("
               "symbol, '.XSHG','.SHSE'), '.XSHE','.SZSE'), '.BJSE','.BSE'), "
               "'.XBIN','.BINANCE'), '.XOKX','.OKX') "
               f"WHERE symbol !~ '^account#'")
    for table, col in _SYMBOL_COLS + [("security_master", "vt_symbol")]:
        op.execute(f"UPDATE {table} SET {col} = {_chain(col, reverse=True)} "
                   f"WHERE {col} ~ '\\.(XSHG|XSHE|BJSE|XBIN|XOKX)$'")
    op.execute("UPDATE security_master SET exchange = CASE exchange "
               "  WHEN 'XSHG' THEN 'SHSE' WHEN 'XSHE' THEN 'SZSE' WHEN 'BJSE' THEN 'BSE' "
               "  WHEN 'XBIN' THEN 'BINANCE' WHEN 'XOKX' THEN 'OKX' ELSE exchange END")
    op.execute("UPDATE account_permission SET allowed_exchanges = ARRAY("
               "  SELECT CASE x WHEN 'XSHG' THEN 'SHSE' WHEN 'XSHE' THEN 'SZSE' WHEN 'BJSE' THEN 'BSE' "
               "              WHEN 'XBIN' THEN 'BINANCE' WHEN 'XOKX' THEN 'OKX' ELSE x END "
               "  FROM unnest(allowed_exchanges) AS x) "
               "WHERE allowed_exchanges && ARRAY['XSHG','XSHE','BJSE','XBIN','XOKX']")
    op.execute("UPDATE screen_factor_cache SET vt_symbol = "
               "replace(replace(replace(vt_symbol, '.XSHG','.SHSE'), '.XSHE','.SZSE'), '.BJSE','.BSE')")
