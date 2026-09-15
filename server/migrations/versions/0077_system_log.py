"""批25：全局日志两套体系——system_log 表（v3 定稿：三通道发送事件并入，notify_log 撤）。

- system_log：全模块运行事件（logger 统一 handler 落库）+ email/im/sms 发送事件
  （成功=INFO/失败及重试耗尽=ERROR/入队待重发=WARN，module=email|im|sms）
- audit_log 不动；task_logs 保留（live-task 时间线+任务详情）；email_outbox 完全不动
- GC：system_log>30d（cleanup_logs beat 任务批删）；audit 只插不删

Revision ID: 0077
Revises: 0076
Create Date: 2026-09-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0077"
down_revision: str = "0076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("module", sa.String(60), nullable=False, server_default=""),
        sa.Column("message", sa.Text(), nullable=False),
    )
    op.create_index("idx_system_log_ts", "system_log", ["ts"])
    op.create_index("idx_system_log_level", "system_log", ["level"])


def downgrade() -> None:
    op.drop_index("idx_system_log_level", table_name="system_log")
    op.drop_index("idx_system_log_ts", table_name="system_log")
    op.drop_table("system_log")
