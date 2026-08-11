"""strategy_account 表（C6 #27）：账户-策略绑定（策略绑定账户/资金）。

strategy_runner（C2）按绑定账户跑实盘：strategy_id 关联 strategy_config.id，
account_id + broker_provider 定 Broker 取凭证，initial_capital/leverage 定资金/杠杆。
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0018"  # B2=0018 当前 head
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "strategy_account",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.Text(), nullable=False),    # 关联 strategy_config.id
        sa.Column("account_id", sa.Text(), nullable=False),     # 账户标识
        sa.Column("broker_provider", sa.Text()),                 # xtp/binance/okx
        sa.Column("initial_capital", sa.Numeric(), server_default="1000000"),
        sa.Column("leverage", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("strategy_id", "account_id"),
    )
    op.create_index("ix_strategy_account_strategy_id", "strategy_account", ["strategy_id"])


def downgrade():
    op.drop_index("ix_strategy_account_strategy_id", table_name="strategy_account")
    op.drop_table("strategy_account")
