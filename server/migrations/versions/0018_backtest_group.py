"""回测组表（B2 #19/#22）：pools + pool_symbols + backtest_runs + backtest_symbols。

两层级（run + symbol），单只是组 N=1。backtest_runs.task_id 关联 PT1 tasks 表。
表结构：docs/回测组设计.md §2。
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0019"  # A4=0019 当前 head
branch_labels = None
depends_on = None


def upgrade():
    # pools（标的池）
    op.create_table(
        "pools",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "pool_symbols",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pool_id", sa.Text(), sa.ForeignKey("pools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.UniqueConstraint("pool_id", "symbol"),
    )
    # backtest_runs（任务级）
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_config_id", sa.Text()),
        sa.Column("symbols", sa.Text(), nullable=False),            # JSON array
        sa.Column("params", sa.Text(), nullable=False),            # JSON
        sa.Column("mode", sa.Text(), nullable=False, server_default="single"),  # single/parallel/serial
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),  # pending/running/done/error
        sa.Column("summary_metrics", sa.Text()),                    # JSON（平均+排名）
        sa.Column("task_id", sa.String(length=64)),                 # 关联 tasks.id（PT1）
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    # backtest_symbols（标的级）
    op.create_table(
        "backtest_symbols",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("result", sa.Text()),                            # JSON
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("run_id", "symbol"),
    )


def downgrade():
    op.drop_table("backtest_symbols")
    op.drop_table("backtest_runs")
    op.drop_table("pool_symbols")
    op.drop_table("pools")
