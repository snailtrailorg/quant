"""cb_daily 转债日行情表(Web 补全批2,05 §5.9 选股器转债双低列)。

双低 = 转债收盘价 + 100×转股价/正股价 → 需要转债日行情(close)与正股行情。
cb_basic_info 已有(静态:转股价/到期日);本迁移补日行情管道表。
ETF 规模/费率字段并入 etf_basic_info(ALTER,可选列)。

Revision ID: 0057
Revises: 0056
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0057"
down_revision: str = "0056"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS cb_daily (
        id BIGSERIAL PRIMARY KEY,
        ts_code TEXT NOT NULL,
        trade_date DATE NOT NULL,
        close NUMERIC,
        pre_close NUMERIC,
        change_pct NUMERIC,
        volume NUMERIC,
        amount NUMERIC,
        UNIQUE (ts_code, trade_date)
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cb_daily_date ON cb_daily (trade_date DESC)")
    # ETF 补列(规模万元/管理费率%/跟踪误差)
    op.execute("ALTER TABLE etf_basic_info ADD COLUMN IF NOT EXISTS fund_scale NUMERIC")
    op.execute("ALTER TABLE etf_basic_info ADD COLUMN IF NOT EXISTS management_fee NUMERIC")
    op.execute("ALTER TABLE etf_basic_info ADD COLUMN IF NOT EXISTS tracking_error NUMERIC")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cb_daily")
    op.execute("ALTER TABLE etf_basic_info DROP COLUMN IF EXISTS tracking_error")
    op.execute("ALTER TABLE etf_basic_info DROP COLUMN IF EXISTS management_fee")
    op.execute("ALTER TABLE etf_basic_info DROP COLUMN IF EXISTS fund_scale")
