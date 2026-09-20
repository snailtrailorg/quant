"""批 57·M2：路由内核两表（29 号 §五——routing_policy/routing_decision）。

routing_policy：consumer_tag 主键+三因子权重（weights jsonb 三键 CHECK）+bulkhead 覆写
+交易切换确认时限（M6 消费，本批只建列）。
routing_decision：审计行（req_summary 可解释+cause 四枚举 CHECK）。

Revision ID: 0093
Revises: 0092
"""
from alembic import op
import sqlalchemy as sa

revision = "0093"
down_revision = "0092"


def upgrade() -> None:
    op.create_table(
        "routing_policy",
        sa.Column("consumer_tag", sa.Text(), primary_key=True),
        sa.Column("weights", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("bulkhead_defaults", sa.dialects.postgresql.JSONB()),   # per-adapter 并发闸门覆写（NULL=Quality.rate_profile 派生初值）
        sa.Column("trade_switch_confirm_timeout_s", sa.Integer(), nullable=False, server_default="300"),  # M6 消费（人审信息包时限）
        sa.CheckConstraint("weights ?& ARRAY['completeness','cost','latency']", name="ck_routing_weights_keys"),
    )
    op.create_table(
        "routing_decision",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("req_summary", sa.dialects.postgresql.JSONB(), nullable=False),   # kind+symbols 数+mode——审计可解释（四审架构）
        sa.Column("chain", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("cause", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "cause IN ('resolve','failover','skip_busy','skip_unhealthy')", name="ck_routing_cause"),
    )
    op.create_index("ix_routing_decision_ts", "routing_decision", ["ts"])

    # 种子：三典型 consumer 权重（28 §6.1——backtest 完整性优先/live 时延优先/缺省保守序）
    op.get_bind().exec_driver_sql("""
      INSERT INTO routing_policy (consumer_tag, weights) VALUES
        ('default',  '{"completeness": 0.5, "cost": 0.3, "latency": 0.2}'),
        ('backtest', '{"completeness": 0.7, "cost": 0.2, "latency": 0.1}'),
        ('live',     '{"completeness": 0.3, "cost": 0.1, "latency": 0.6}')
      ON CONFLICT (consumer_tag) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index("ix_routing_decision_ts", table_name="routing_decision")
    op.drop_table("routing_decision")
    op.drop_table("routing_policy")
