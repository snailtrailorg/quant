"""批177 收尾：迁移窗口期旧进程写入的旧后缀行，做一次幂等清扫（仅热状态表）。

背景：release 阶段 5 跑迁移、阶段 7 才重启 celery/task；两者之间旧代码仍会按旧口径写
reconcile_issue / risk_log / order_log 等状态表。0183 只转当时存量，窗口期新增行会漏。
本迁移只扫**写入频繁且行数小**的状态表（不含 bar_* 千万级表——那些只由新代码写）。

幂等：按旧后缀正则匹配，重复执行 0 行。

Revision ID: 0185
Revises: 0184
"""
from alembic import op

revision = "0185"
down_revision = "0184"

_TABLES = [
    ("position_snapshot", "symbol"), ("reconcile_issue", "symbol"),
    ("order_log", "symbol"), ("trade_log", "symbol"), ("risk_log", "symbol"),
    ("signal_log", "symbol"), ("live_task", "symbol"), ("freeze_event", "symbol"),
    ("sync_gap", "symbol"), ("pool_symbols", "symbol"), ("backtest_symbols", "symbol"),
    ("strategy_config", "symbol"), ("paper_trade_log", "symbol"),
]

_OLD = r"\.(SHSE|SZSE|BSE|BINANCE|OKX)$"


def _chain(col: str) -> str:
    return ("replace(replace(replace(replace(replace({c}, '.SHSE','.XSHG'), '.SZSE','.XSHE'), "
            "'.BSE','.BJSE'), '.BINANCE','.XBIN'), '.OKX','.XOKX')").format(c=col)


def upgrade() -> None:
    op.execute("SET LOCAL statement_timeout = 0")
    # 先并后转：窗口期新旧写法可能**同时存在**（旧进程写旧、新进程写新）——
    # 直接 UPDATE 会撞唯一键（prod 实证：ux_reconcile_issue_open 511010）。
    # 1) reconcile_issue：同 (归一 symbol, issue_type) 已有 open 的，删旧写法那条
    op.execute(f"""
        DELETE FROM reconcile_issue b
        USING (SELECT r.id, {_chain('r.symbol')} AS new_sym, r.issue_type
               FROM reconcile_issue r
               WHERE r.symbol ~ '{_OLD}' AND r.status = 'open') d
        WHERE b.id = d.id
          AND EXISTS (SELECT 1 FROM reconcile_issue v
                      WHERE v.symbol = d.new_sym AND v.issue_type = d.issue_type
                        AND v.status = 'open')
    """)
    # 2) position_snapshot：同 (account, 归一 symbol, direction) 保 ts 最新
    op.execute(f"""
        DELETE FROM position_snapshot b
        USING (SELECT p.ctid AS cid,
                      row_number() OVER (PARTITION BY p.account_id, {_chain('p.symbol')}, p.direction
                                         ORDER BY p.ts DESC NULLS LAST, p.ctid DESC) AS rn
               FROM position_snapshot p WHERE p.symbol ~ '{_OLD}') d
        WHERE b.ctid = d.cid AND d.rn > 1
    """)
    # 3) pool_symbols：同 (pool_id, 归一 symbol) 只留一条
    op.execute(f"""
        DELETE FROM pool_symbols b
        USING (SELECT p.ctid AS cid,
                      row_number() OVER (PARTITION BY p.pool_id, {_chain('p.symbol')}) AS rn
               FROM pool_symbols p WHERE p.symbol ~ '{_OLD}') d
        WHERE b.ctid = d.cid AND d.rn > 1
    """)
    for table, col in _TABLES:
        op.execute(f"UPDATE {table} SET {col} = {_chain(col)} WHERE {col} ~ '{_OLD}'")


def downgrade() -> None:
    # 不回滚：与 0183 同批回滚时由 0183.downgrade 统一反向；单独回滚本迁移无意义。
    pass
