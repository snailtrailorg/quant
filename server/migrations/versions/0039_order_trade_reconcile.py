"""order_log/trade_log 补对账列（SC1 批次，2026-08-17 稳定性检查 F-4/F-7/F-8/#46）

- order_log.client_order_id：策略侧幂等/对账键（WAL submitting→submitted 流转携带）
- order_log.error：send_failed 原因
- trade_log.strategy_id / trade_ref：成交归属 + 券商成交号幂等（重启/重复回调不重插）

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0039"
down_revision: str = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE order_log ADD COLUMN IF NOT EXISTS client_order_id TEXT")
    op.execute("ALTER TABLE order_log ADD COLUMN IF NOT EXISTS error TEXT")
    op.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS strategy_id TEXT")
    op.execute("ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS trade_ref TEXT")
    # 成交号唯一（幂等）：重复事件/重启补录靠它去重（PG 唯一索引允许多 NULL，无需部分索引）
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_log_ref ON trade_log (trade_ref)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_trade_log_ref")
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS trade_ref")
    op.execute("ALTER TABLE trade_log DROP COLUMN IF EXISTS strategy_id")
    op.execute("ALTER TABLE order_log DROP COLUMN IF EXISTS error")
    op.execute("ALTER TABLE order_log DROP COLUMN IF EXISTS client_order_id")
