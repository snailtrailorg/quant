"""account_snapshot 加 available_cash 列（DB 优化批，2026-08-21 审计 F4.1 根修）。

PERCENT/ALL_IN 原口径=全账户总值-持仓成本价近似（多策略共账户合计超配+上涨市高估可用）。
加可用资金列（XTP 无直出，vnpy AccountData.balance - frozen 近似现金账户可用），
策略 sizing 优先读它，快照无该列数据时退化旧口径。

Revision ID: 0050
Revises: 0049
Create Date: 2026-08-21
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0050"
down_revision: str = "0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("account_snapshot",
                  sa.Column("available_cash", sa.Numeric(), server_default=sa.text("NULL")))


def downgrade() -> None:
    op.drop_column("account_snapshot", "available_cash")
