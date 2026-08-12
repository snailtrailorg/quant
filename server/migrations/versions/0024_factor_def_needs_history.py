"""factor_def 加 needs_history 列（因子静态/动态区分）。

needs_history=0 -> 静态因子（只用当前 bar，可用于选股+策略）
needs_history>0 -> 动态因子（需要历史窗口，只能用于策略）

migration 0024：在 0023 factor_def 表上加列。
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("factor_def", sa.Column("needs_history", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("factor_def", "needs_history")