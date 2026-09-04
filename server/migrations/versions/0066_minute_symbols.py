"""minute_symbols 展开表 + minute_data_source 开关（分钟数据源重构，2026-09-04）

方案 docs/architecture/21-分钟数据源设计.md §3.1/3.4：
- 建 minute_symbols(symbol PK, source, updated_at)——攒数据标的展开表（池级∪个股级统一，
  一标的只一行，source 纯信息列 direct>pool）
- system_config 插 minute_data_source='tencent'（数据源开关：tencent/tushare 单选互斥）
- backfill：现有 minute_history_start 的 astock 池成员展开进表——不 backfill 上产即
  静默丢存量池的分钟收集（盲审 A-P1）

Revision ID: 0066
Revises: 0065
Create Date: 2026-09-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0066"
down_revision: str = "0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "minute_symbols",
        sa.Column("symbol", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description) VALUES "
        "('minute_data_source', 'tencent', 'string', "
        "'分钟数据源（tencent=腾讯攒过渡 / tushare=Tushare 分钟线终极，互斥单选）') "
        "ON CONFLICT (key) DO NOTHING"
    )
    # backfill：现有 minute_history_start 的 astock 池成员展开进表
    op.execute(
        "INSERT INTO minute_symbols (symbol, source) "
        "SELECT DISTINCT ON (ps.symbol) ps.symbol, 'pool:' || p.id "
        "FROM pool_symbols ps JOIN pools p ON p.id = ps.pool_id "
        "WHERE p.minute_history_start IS NOT NULL AND p.category = 'astock' "
        "ON CONFLICT (symbol) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM system_config WHERE key='minute_data_source'")
    op.drop_table("minute_symbols")
