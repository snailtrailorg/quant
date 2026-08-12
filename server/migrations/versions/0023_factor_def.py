"""factor_def 用户自定义因子表（因子平台化 #3）。

用户可在 Web 上写 Python 代码创建自定义因子，保存在 DB，供 DSL/Python 策略引用。
预置因子（@register_factor）保留，与自定义因子统一注册表。

migration 0023：建 factor_def 表。
"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "factor_def",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("category", sa.Text(), nullable=False, server_default="custom"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("params", sa.Text(), nullable=False, server_default="{}"),  # JSON
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("factor_def")