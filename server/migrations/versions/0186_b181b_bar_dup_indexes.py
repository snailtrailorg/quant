"""批181b：删除 bar_* 表的重复 (symbol,ts) 索引（唯一约束索引已覆盖）。

病根：0001/0022/0064/0065 四个迁移统一模板 = `UNIQUE(symbol, ts)`（自动建
`bar_*_symbol_ts_key`）+ 手工 `CREATE INDEX idx_bar_*_symbol_ts (symbol, ts DESC)`，
同一键两份索引。B-tree 可反向扫描，单标的查询等价；唯一索引已覆盖同样键。

在线删：DROP INDEX CONCURRENTLY（autocommit_block——事务内禁止 CONCURRENTLY）。
downgrade 重建普通索引（CONCURRENTLY，仅表存在时），保证往返。

Revision ID: 0186
Revises: 0185
"""
from alembic import op

revision = "0186"
down_revision = "0185"

_TABLES = ["bar_1d", "bar_1min", "bar_5min", "bar_15min", "bar_30min",
           "bar_60min", "bar_1h", "bar_4h", "bar_index"]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for t in _TABLES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS idx_{t}_symbol_ts")


def downgrade() -> None:
    bind = op.get_bind()
    present = [t for t in _TABLES if bind.dialect.has_table(bind, t)]
    with op.get_context().autocommit_block():
        for t in present:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_{t}_symbol_ts "
                f"ON {t} (symbol, ts DESC)")
