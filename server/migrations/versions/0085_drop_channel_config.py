"""批39：channel_config 表 drop（死码连根——用户裁定）。

webhook 推送链整体退役（Channels UI 批38 删/端点+channel.py 批39 删），表零读者。
Downgrade 重建空表（数据不复活——旧链已退役，恢复配置走订阅链）。
Revision ID: 0085
Revises: 0084
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0085"
down_revision = "0084"


def upgrade() -> None:
    op.drop_table("channel_config")


def downgrade() -> None:
    op.create_table(
        "channel_config",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("credentials_encrypted", sa.Text()),
        sa.Column("params", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
