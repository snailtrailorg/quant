"""add trade_day_filter to sync_config + schedule 改 cron 表达式

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-02

- 加 trade_day_filter 列（none/workday/trade_day，默认 trade_day）
- schedule 枚举值（daily_after_close/weekly/yearly）数据迁移为 cron 表达式
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 加 trade_day_filter 列
    op.add_column("sync_config", sa.Column("trade_day_filter", sa.Text(), server_default="trade_day"))

    # 2. schedule 枚举 -> cron 表达式（数据迁移）
    op.execute("UPDATE sync_config SET schedule='30 16 * * 1-5' WHERE schedule='daily_after_close'")
    op.execute("UPDATE sync_config SET schedule='0 9 * * 1' WHERE schedule='weekly'")
    op.execute("UPDATE sync_config SET schedule='0 9 1 1 *' WHERE schedule='yearly'")

    # 3. trade_day_filter 默认值（按数据类型）
    op.execute("UPDATE sync_config SET trade_day_filter='trade_day' WHERE id IN ('astock_daily','astock_basic','cb_daily','etf_daily')")
    op.execute("UPDATE sync_config SET trade_day_filter='none' WHERE id IN ('astock_list','cb_basic','etf_list','trade_cal')")


def downgrade() -> None:
    # schedule cron -> 枚举（反向，可能不精确）
    op.execute("UPDATE sync_config SET schedule='daily_after_close' WHERE schedule='30 16 * * 1-5'")
    op.execute("UPDATE sync_config SET schedule='weekly' WHERE schedule='0 9 * * 1'")
    op.execute("UPDATE sync_config SET schedule='yearly' WHERE schedule='0 9 1 1 *'")
    op.drop_column("sync_config", "trade_day_filter")
