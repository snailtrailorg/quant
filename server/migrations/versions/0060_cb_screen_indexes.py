"""cb 选股慢查询根修：bar_1d(ts)/daily_basic(trade_date) 索引（W6 收官日发现）。

prod 冒烟实证 /api/screen/cb HTTP 500@10.17s——18 号 web 10s 语句超时掐掉：
SQL 两个无关联 max() 子查询（bar_1d.max(ts)/daily_basic.max(trade_date)）在
symbol 打头索引上用不上 → 全表扫（prod bar_1d 百万级）。dev/冒烟历史绿是小表掩盖。

18 号规范：索引上生产 CREATE INDEX CONCURRENTLY（不锁写）。
健壮性：daily_basic 等表在分库/staging 可能不存在（Tushare 未同步）——表存在才建；
IF NOT EXISTS 幂等（CONCURRENTLY 中断不可回滚，残留半成品可重跑）。

Revision ID: 0060
Revises: 0059
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0060"
down_revision: str = "0059"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None

_IDX = [("bar_1d", "idx_bar_1d_ts", "ts"),
        ("daily_basic", "idx_daily_basic_trade_date", "trade_date")]


def upgrade() -> None:
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        for tbl, idx, col in _IDX:
            if bind.dialect.has_table(bind, tbl):
                op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {idx} ON {tbl} ({col})")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for tbl, idx, col in _IDX:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {idx}")
