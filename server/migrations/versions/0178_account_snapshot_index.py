"""批 172c P2 收口：account_snapshot 查询索引（每单现读路径）。"""
from typing import Sequence, Union

from alembic import op

revision: str = "0178"
down_revision: Union[str, Sequence[str], None] = "0177"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 每单现读取最新一行：走 (account_id, ts DESC) 索引，避免逐单全表排序
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_account_snapshot_acct_ts "
                   "ON account_snapshot (account_id, ts DESC)")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_account_snapshot_acct_ts")
