"""risk_rules 表（风控规则配置 DB 化，平台化风控 PT6）。

RiskRule 接口抽象：别人实现 RiskRule 接入自己的规则。
当前实现 MaxPosition/MaxSingleOrder/DailyLossLimit（risk_control DEFAULT_RULES 子集）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "risk_rules",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),             # 规则显示名
        sa.Column("type", sa.Text(), nullable=False),             # max_position/max_single_order/daily_loss_limit
        sa.Column("params", sa.Text()),                          # JSON: {max_pct:0.1} 等
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_risk_rules_type", "risk_rules", ["type"])


def downgrade():
    op.drop_index("ix_risk_rules_type", table_name="risk_rules")
    op.drop_table("risk_rules")
