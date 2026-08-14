"""email_outbox 发件箱表（邮件持久化 + 指数退避重发）

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-14

邮件先落库再发（进程重启不丢）；失败由 Celery beat 每分钟扫描重发，
退避 60*2^(n-1) 秒（1→2→4→8→16→30 分钟封顶），6 次失败标 failed。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029"
down_revision: str = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("to_email", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),  # pending/sending/sent/failed
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),   # 已失败次数
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_email_outbox_status_next", "email_outbox", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_email_outbox_status_next", table_name="email_outbox")
    op.drop_table("email_outbox")
