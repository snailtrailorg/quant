"""notifications 通知中心表（站内通知，持久化，前台铃铛闭环）

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-14

事件源（email 最终失败/任务失败/风控/数据/系统）统一落此表；
前台顶栏铃铛轮询 active 数量，点击跳转对应页面，支持全部确认（ack）。
替代原 Valkey alert:history（易失，2026-08-14 决策直接切换，历史数据不迁移）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030"
down_revision: str = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("level", sa.Text(), nullable=False),        # info / warn / critical
        sa.Column("category", sa.Text(), nullable=False),     # email / risk / task / data / system
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("source_ref", sa.Text()),                   # 来源引用（如 email_outbox.id）
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),  # active / acked
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("acked_by", sa.Text()),
        sa.Column("acked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_notifications_status_created", "notifications", ["status", "created_at"])
    op.create_index("ix_notifications_category", "notifications", ["category"])


def downgrade() -> None:
    op.drop_index("ix_notifications_category", table_name="notifications")
    op.drop_index("ix_notifications_status_created", table_name="notifications")
    op.drop_table("notifications")
