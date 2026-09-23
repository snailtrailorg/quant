"""多账号源 D2 段1：加 venue_id/account_key 列（可空，回填+约束在 0100）。

- external_interface.account_key：语义键（资金账号），UNIQUE(provider,account_key)。
  可空起步——资金账号在加密凭证里，SQL 迁不出来，由用户经 CRUD 填（多 NULL 合法）。
- live_task/position_snapshot/position_refresh/account_snapshot/order_log/trade_log
  各加 venue_id（可空，回填+NOT NULL+FK 在 0100）。

Revision ID: 0099
Revises: 0098
"""
from alembic import op
import sqlalchemy as sa

revision = "0099"
down_revision = "0098"


def upgrade() -> None:
    op.add_column("external_interface", sa.Column("account_key", sa.Text(), nullable=True))
    op.create_index("ix_external_interface_account_key", "external_interface",
                    ["provider", "account_key"], unique=True)
    op.add_column("live_task", sa.Column("venue_id", sa.BigInteger(), nullable=True))
    op.add_column("position_snapshot", sa.Column("venue_id", sa.BigInteger(), nullable=True))
    op.add_column("position_refresh", sa.Column("venue_id", sa.BigInteger(), nullable=True))
    op.add_column("account_snapshot", sa.Column("venue_id", sa.BigInteger(), nullable=True))
    op.add_column("order_log", sa.Column("venue_id", sa.BigInteger(), nullable=True))
    op.add_column("trade_log", sa.Column("venue_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("trade_log", "venue_id")
    op.drop_column("order_log", "venue_id")
    op.drop_column("account_snapshot", "venue_id")
    op.drop_column("position_refresh", "venue_id")
    op.drop_column("position_snapshot", "venue_id")
    op.drop_column("live_task", "venue_id")
    op.drop_index("ix_external_interface_account_key", table_name="external_interface")
    op.drop_column("external_interface", "account_key")
