"""broker_config provider 迁移 binance→binance_perp / okx→okx_perp（26 号收尾批 C key 对齐）。

broker.py _REGISTRY 与 adapters.py create_adapter 的 key 命名对齐为 *_perp；broker_config
存量行若存 'binance'/'okx' 迁移到 *_perp。加密实盘未开通（外部 gate），预期无存量行，
迁移显式声明已核对。

Revision ID: 0069
Revises: 0068
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0069"
down_revision: str = "0068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE broker_config SET provider='binance_perp' WHERE provider='binance'")
    op.execute("UPDATE broker_config SET provider='okx_perp' WHERE provider='okx'")


def downgrade() -> None:
    op.execute("UPDATE broker_config SET provider='binance' WHERE provider='binance_perp'")
    op.execute("UPDATE broker_config SET provider='okx' WHERE provider='okx_perp'")
