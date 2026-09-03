"""bar_index 表（回测基准指数日线，ptrade 全家桶批 1，2026-09-04）

沪深300（000300.SHSE）等基准指数独立存 bar_index，与股票 bar_1d 分离——避免被
data_sync 的 per-symbol 股票同步/品类校验误处理。结构对齐 bar_1d（含 freq 固定 1D），
adj_factor 恒 NULL（指数无复权，16 号「NULL 因子=1.0 降级」）。

Revision ID: 0065
Revises: 0064
Create Date: 2026-09-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0065"
down_revision: str = "0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bar_index",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("freq", sa.Text(), nullable=False, server_default="1D"),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(), nullable=False),
        sa.Column("high", sa.Numeric(), nullable=False),
        sa.Column("low", sa.Numeric(), nullable=False),
        sa.Column("close", sa.Numeric(), nullable=False),
        sa.Column("volume", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("amount", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("adj_factor", sa.Numeric()),
        sa.Column("source", sa.Text(), nullable=False, server_default="tushare"),
        sa.UniqueConstraint("symbol", "ts"),
    )
    op.create_index("idx_bar_index_symbol_ts", "bar_index", ["symbol", sa.text("ts DESC")])


def downgrade() -> None:
    op.drop_index("idx_bar_index_symbol_ts", table_name="bar_index")
    op.drop_table("bar_index")
