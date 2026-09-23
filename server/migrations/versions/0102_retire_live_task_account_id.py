"""多账号源 D2 段4：删 live_task.account_id（身份线统一收尾）。

live_task.venue_id（0099 加列 + 0100 回填 NOT NULL + FK RESTRICT）已取代 account_id
（策略级自由文本）成为唯一身份线。删列前：position_snapshot/position_refresh 的
account_id 已删（0100）、strategy_account 表已退役（0101）——本迁移收尾删
live_task.account_id。

Revision ID: 0102
Revises: 0101
"""
from alembic import op
import sqlalchemy as sa

revision = "0102"
down_revision = "0101"


def upgrade() -> None:
    op.drop_column("live_task", "account_id")


def downgrade() -> None:
    op.add_column("live_task", sa.Column("account_id", sa.Text(), nullable=True))
