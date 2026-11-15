"""批183：daily_basic 重复索引清理（运行时 DDL 遗留 + 0001 冗余）。

- idx_daily_basic_code_date：tushare_adapter 历史运行时 CREATE INDEX（死代码已删），
  与 idx_daily_basic_ts_code 定义完全相同。
- idx_daily_basic_ts_code：0001 建，与 UNIQUE(ts_code, trade_date) 同键（B-tree 可反向）。
保留：daily_basic_ts_code_trade_date_key（UNIQUE）、idx_daily_basic_trade_date。

Revision ID: 0187
Revises: 0186
"""
from alembic import op

revision = "0187"
down_revision = "0186"

_IDX = ["idx_daily_basic_code_date", "idx_daily_basic_ts_code"]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for i in _IDX:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {i}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for i in _IDX:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {i} "
                f"ON daily_basic (ts_code, trade_date DESC)")
