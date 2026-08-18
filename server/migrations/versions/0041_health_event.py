"""health_event 表（15-服务监控设计 Phase 1）

- 健康监控模块的判定历史：触发/恢复沿事件落库（可追溯 + 前端健康页 + 外部审计）
- 由 src/health_monitor/monitor.py run_check() 写入，只追加不更新

Revision ID: 0041
Revises: 0040
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0041"
down_revision: str = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "health_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("component", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),   # critical|warning|recovery
        sa.Column("detail", sa.Text()),
    )
    op.create_index("idx_health_event_ts", "health_event", ["ts"])


def downgrade() -> None:
    op.drop_index("idx_health_event_ts", table_name="health_event")
    op.drop_table("health_event")
