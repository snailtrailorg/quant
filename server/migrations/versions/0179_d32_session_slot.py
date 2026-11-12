# EXPAND-CONTRACT: phase=expand pair=0180
"""批 172d P3 expand：会话租约表 + 双轨 impl 列（不含索引切换，切换在 0180 contract）。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0179"
down_revision: Union[str, Sequence[str], None] = "0178"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_session_slot",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("terminal_id", sa.Integer(), nullable=False),
        sa.Column("holder", sa.Text(), nullable=False),
        sa.Column("boot_epoch", sa.BigInteger(), nullable=False),
        sa.Column("heartbeat", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("account_id", "provider", "terminal_id"),
    )

    op.add_column("trading_account", sa.Column(
        "impl", sa.Text(), nullable=False, server_default="legacy"))
    op.create_check_constraint("ck_trading_account_impl", "trading_account",
                               "impl IN ('legacy','plugin')")


def downgrade() -> None:
    op.drop_constraint("ck_trading_account_impl", "trading_account", type_="check")
    op.drop_column("trading_account", "impl")
    op.drop_table("provider_session_slot")
