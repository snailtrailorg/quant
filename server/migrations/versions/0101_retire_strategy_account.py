"""多账号源 D2 段3：strategy_account 退役（venue 化收编——身份线统一）。

- initial_capital → per-venue 资金基线（首条快照 total_value，_account_baseline_capital 已实现）
- leverage → venue 级配置（账号级杠杆）；现为死参数（order dict 无 leverage 键恒 1，
  双盲审 B-P2-6），且加密 venue 未开通——leverage 实配下发留待加密接入批
- broker_provider → 死列（从未驱动选源，选源硬编码 XTPAdapter；D5 改 venue.provider）

Revision ID: 0101
Revises: 0100
"""
from alembic import op

revision = "0101"
down_revision = "0100"


def upgrade() -> None:
    op.drop_index("ix_strategy_account_strategy_id", table_name="strategy_account")
    op.drop_table("strategy_account")


def downgrade() -> None:
    import sqlalchemy as sa
    op.create_table(
        "strategy_account",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("broker_provider", sa.Text()),
        sa.Column("initial_capital", sa.Numeric(), server_default="1000000"),
        sa.Column("leverage", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("strategy_id", "account_id"),
    )
    op.create_index("ix_strategy_account_strategy_id", "strategy_account", ["strategy_id"])
