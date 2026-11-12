# EXPAND-CONTRACT: phase=contract pair=0179
"""批 172d P3 contract：account_key 部分唯一换维（provider → market，防 xtp/emt 双行同账号）。"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0180"
down_revision: Union[str, Sequence[str], None] = "0179"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 预检：同 (market, account_key) 重复（enabled 且 key 非空）→ raise，禁静默合并
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
        op.drop_index("ix_trading_account_account_key", table_name="trading_account",
                      if_exists=True, postgresql_concurrently=True)
        op.drop_index("uq_trading_account_market_account_key", table_name="trading_account",
                      if_exists=True, postgresql_concurrently=True)
        op.create_index("uq_trading_account_market_account_key", "trading_account",
                        ["market", "account_key"], unique=True,
                        postgresql_concurrently=True,
                        postgresql_where=sa.text("account_key IS NOT NULL AND enabled"))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("uq_trading_account_market_account_key", table_name="trading_account",
                      if_exists=True, postgresql_concurrently=True)
        op.create_index("ix_trading_account_account_key", "trading_account",
                        ["provider", "account_key"], unique=True,
                        postgresql_concurrently=True)
