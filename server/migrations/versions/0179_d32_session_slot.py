"""批 172d P3：XTP 会话号段租约表 + 双轨 impl 列 + account_key 部分唯一。"""
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

    # account_key 部分唯一：重复行预检（enabled 且 key 非空）→ 有重复 raise，禁静默
    op.execute("""
        DO $$
        DECLARE n int;
        BEGIN
          SELECT count(*) INTO n FROM (
            SELECT market, account_key FROM trading_account
            WHERE account_key IS NOT NULL AND enabled
            GROUP BY market, account_key HAVING count(*) > 1
          ) x;
          IF n > 0 THEN
            RAISE EXCEPTION 'trading_account 存在 % 组 (market, account_key) 重复，先人工合并', n;
          END IF;
        END $$;
    """)
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_trading_account_account_key")
        op.execute("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
                   "uq_trading_account_market_account_key ON trading_account (market, account_key) "
                   "WHERE account_key IS NOT NULL AND enabled")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_trading_account_market_account_key")
        op.execute("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
                   "ix_trading_account_account_key ON trading_account (provider, account_key)")
    op.drop_constraint("ck_trading_account_impl", "trading_account", type_="check")
    op.drop_column("trading_account", "impl")
    op.drop_table("provider_session_slot")
