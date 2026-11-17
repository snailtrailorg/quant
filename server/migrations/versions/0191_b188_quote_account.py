"""批188：trading_account 增 quote_account_id（TD→行情账号取价映射）。

TD 与行情账号解耦后，清仓取价读 hub:latest_tick:{quote}:{vt}，需知道本交易账号对应哪个行情账号。
不填则默认自身（td=quote，兼容现状）。必须显式回填存量行——PG 列 DEFAULT 不能引用 id，
不回填则反查空→hub 不订阅→无 tick。

expand 型（加列+回填），非破坏性，无需两步走。

Revision ID: 0191
Revises: 0190
"""
from alembic import op

revision = "0191"
down_revision = "0190"


def upgrade() -> None:
    op.execute("ALTER TABLE trading_account ADD COLUMN IF NOT EXISTS quote_account_id integer")
    op.execute("UPDATE trading_account SET quote_account_id = id WHERE quote_account_id IS NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE trading_account DROP COLUMN IF EXISTS quote_account_id")
