"""order_log 加 vt_orderid 列（F-50 重启后成交关联，2026-09-03）

- order_log.vt_orderid：vnpy 委托号（形如 XTP.xxx）——重启后 _vt2cid 进程内存丢失，
  write_trade_log 无法从 client_order_id 反查 order_id（order_id NULL → reconcile_three_books
  的「委托不成交」把已成交单误判为不成交）。加列后按 vt_orderid 反查，消除误判。

Revision ID: 0063
Revises: 0062
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0063"
down_revision: str = "0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE order_log ADD COLUMN IF NOT EXISTS vt_orderid TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE order_log DROP COLUMN IF EXISTS vt_orderid")
