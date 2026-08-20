"""daily_basic trade_date 单列索引（三档项 5 选股性能，2026-08-20 补盲审 B7）。

横截面 SQL 的 latest CTE（MAX(trade_date)<=日）与 45 日窗口聚合每请求扫全表——
现 7.7 万行无感，表随日频全市场（~5548 行/日）线性增长，viewer 端点按需触发。
现有索引仅 (ts_code, trade_date DESC) 复合，不覆盖纯日期谓词。

Revision ID: 0049
Revises: 0048
Create Date: 2026-08-20
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0049"
down_revision: str = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("idx_daily_basic_trade_date", "daily_basic", ["trade_date"])


def downgrade() -> None:
    op.drop_index("idx_daily_basic_trade_date", table_name="daily_basic")
