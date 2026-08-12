"""live_task 表 + backtest_runs.symbol_params 列（策略与实盘/回测任务分离）。

策略与标的解耦：
- strategy_config 为策略配方（因子/DSL/Python代码/参数定义），不绑标的
- live_task 为实盘任务（绑定策略+标的+任务参数值），一标的一进程
- backtest_runs 加 symbol_params 支持回测 per-symbol 参数覆盖

migration 0025：在 0024 之上建 live_task + 加 backtest_runs.symbol_params 列。
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    # 1. live_task 表（实盘任务）
    op.create_table(
        "live_task",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("strategy_id", sa.Text(), nullable=False),           # 关联 strategy_config.id
        sa.Column("symbol", sa.Text(), nullable=False),                 # 单标的（一标的一进程）
        sa.Column("params", sa.Text(), nullable=False, server_default="{}"),  # JSON 任务级参数值
        sa.Column("strategy_snapshot", sa.Text(), nullable=False, server_default="{}"),  # JSON 策略快照
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),  # pending/running/stopped/error
        sa.Column("task_id", sa.Text(), nullable=True),                # 关联 tasks.id
        sa.Column("systemd_unit", sa.Text(), nullable=True),
        sa.Column("account_id", sa.Text(), nullable=True),
        sa.Column("initial_capital", sa.Numeric(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_live_task_strategy_id", "live_task", ["strategy_id"])
    op.create_index("ix_live_task_status", "live_task", ["status"])

    # 2. backtest_runs 加 symbol_params 列
    op.add_column("backtest_runs",
                  sa.Column("symbol_params", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("backtest_runs", "symbol_params")
    op.drop_index("ix_live_task_status", "live_task")
    op.drop_index("ix_live_task_strategy_id", "live_task")
    op.drop_table("live_task")