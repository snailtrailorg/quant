"""sync_config 加 provider 列（24 号多数据源架构，任务级数据源路由）。

每条同步任务独立指定数据源（tushare/joinquant/ricequant），默认 tushare。
历史 K 线数据源无关化：engine 的 K 线路径按 provider 路由到对应 adapter。

Revision ID: 0067
Revises: 0066
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0067"
down_revision: str = "0066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sync_config", sa.Column("provider", sa.Text(),
                                          nullable=False, server_default="tushare"))


def downgrade() -> None:
    op.drop_column("sync_config", "provider")
