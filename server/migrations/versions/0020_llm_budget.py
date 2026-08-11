"""llm_budget 表（D5 #38）：AI 预算预警阈值。

llm_usage（已有，migration 0011）聚合 vs budget，超 alert_threshold_pct 告警。
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0021"  # C6=0021 当前 head
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_budget",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text()),                       # 限某 provider，NULL=全局
        sa.Column("daily_token_limit", sa.Integer()),           # 日 token 上限
        sa.Column("monthly_cost_limit", sa.Numeric()),         # 月成本上限（元）
        sa.Column("alert_threshold_pct", sa.Integer(), server_default="80"),  # 预警阈值 %（80%告警）
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade():
    op.drop_table("llm_budget")
