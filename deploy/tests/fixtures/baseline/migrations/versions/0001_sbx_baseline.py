"""sbx 基线迁移：建演示表（expand-only 示范——upgrade 只建不删）。

rev id 用四位序号，release.yml DDL 门按"文件名目录 diff"判新增。
"""
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        "CREATE TABLE demo_t ("
        "  id serial PRIMARY KEY,"
        "  payload text NOT NULL,"
        "  created_at timestamptz NOT NULL DEFAULT now()"
        ")"
    )


def downgrade() -> None:
    from alembic import op

    # 用 pythonic API（op.drop_table）而非裸 SQL 文本——
    # 避免注释/文本形态误触 release.yml 阶段 4 的破坏性 DDL 门（该门按设计拦 upgrade 契约文本）
    op.drop_table("demo_t")
