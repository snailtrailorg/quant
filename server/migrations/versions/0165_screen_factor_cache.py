"""screen_factor_cache：标的筛选预计算因子缓存（批 164a 期 1/3）。

契约（任务文件 v2 步骤 2——双盲 A-P1-2 收编）：
- 键=(category, ts_code, factor, trade_date) 四列主键；value NUMERIC
- 写侧：盘后调度全量算 T-1 日（新日新行，历史保留供 164c DSL 时序扩展）
- 读侧：取 ≤T-1 的 max(trade_date)（T 翻转自动切新日）
- 期 1 因子：momentum_20（20日动量=close/mean(close,20)-1）——其余因子随批次加

Revision ID: 0165
Revises: 0164
"""
from alembic import op
import sqlalchemy as sa

revision = "0165"
down_revision = "0164"


def upgrade():
    op.create_table(
        "screen_factor_cache",
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("factor", sa.Text(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=True),
        sa.PrimaryKeyConstraint("category", "ts_code", "factor", "trade_date"),
    )
    op.create_index("idx_sfc_cat_factor_date", "screen_factor_cache", ["category", "factor", "trade_date"])


def downgrade():
    op.drop_index("idx_sfc_cat_factor_date", table_name="screen_factor_cache")
    op.drop_table("screen_factor_cache")
