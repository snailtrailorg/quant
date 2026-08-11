"""后台任务管理表（PT1 平台化核心）：tasks + task_logs。

统一所有异步任务（回测/同步/AI/策略），卡死检测 + 故障定位 + 强制删除。
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255)),
        sa.Column("type", sa.String(length=32)),                # backtest/sync/ai/trade
        sa.Column("trigger_type", sa.String(length=16)),        # manual/schedule/event
        sa.Column("trigger_user", sa.String(length=64)),
        sa.Column("status", sa.String(length=16)),               # running/paused/completed/failed/terminated/stuck
        sa.Column("progress", sa.Text()),                        # JSON: {current,total,pct,step}
        sa.Column("params", sa.Text()),                          # JSON: 任务参数
        sa.Column("last_heartbeat", sa.DateTime(timezone=True)),
        sa.Column("pid", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.Column("start_time", sa.DateTime(timezone=True)),
        sa.Column("end_time", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])

    op.create_table(
        "task_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(length=64)),
        sa.Column("level", sa.String(length=8)),                 # INFO/WARN/ERROR/DEBUG
        sa.Column("message", sa.Text()),
        sa.Column("step_name", sa.String(length=64)),
        sa.Column("sql_or_api", sa.Text()),                      # 当前 SQL/API（故障定位）
        sa.Column("resource_usage", sa.Text()),                   # JSON: CPU/内存/连接池
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_task_logs_task_id", "task_logs", ["task_id"])


def downgrade():
    op.drop_index("ix_task_logs_task_id", table_name="task_logs")
    op.drop_table("task_logs")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_table("tasks")
