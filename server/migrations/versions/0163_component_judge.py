# 批 155：依赖健康判定——security_component 扩 6 列（全 nullable，rollback 兼容）
# 设计：flow/方案/批155-依赖健康检测重构-设计.md §3.1

revision = "0163"
down_revision = "0162"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("security_component", sa.Column("managed_by", sa.Text(), nullable=True))
    op.add_column("security_component", sa.Column("latest_version", sa.Text(), nullable=True))
    op.add_column("security_component", sa.Column("latest_source", sa.Text(), nullable=True))
    op.add_column("security_component", sa.Column("judge_class", sa.Text(), nullable=True))
    op.add_column("security_component", sa.Column("judge_why", sa.Text(), nullable=True))
    op.add_column("security_component", sa.Column("checked_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade():
    op.drop_column("security_component", "checked_at")
    op.drop_column("security_component", "judge_why")
    op.drop_column("security_component", "judge_class")
    op.drop_column("security_component", "latest_source")
    op.drop_column("security_component", "latest_version")
    op.drop_column("security_component", "managed_by")


from alembic import op
import sqlalchemy as sa
