"""迁移 0044：pools.minute_history_start + 禁用全市场分钟同步（池驱动模式替代）。

- pools 加列 minute_history_start DATE（NULL=该池不拉分钟历史）
- DML 禁用 astock_minute + astock_minute_5min（S-F2：init-seed ON CONFLICT DO NOTHING
  改默认值对已存在行无效；两条共用 _sync_astock_minute handler 遍历全静态列表——
  1 次/分钟限速下是风暴源；池驱动 sync_pools_minute 替代）

Revision ID: 0044
Revises: 0043
Create Date: 2026-08-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0044"
down_revision: str = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE pools ADD COLUMN IF NOT EXISTS minute_history_start DATE")
    op.execute("UPDATE sync_config SET enabled=false WHERE id IN ('astock_minute', 'astock_minute_5min')")


def downgrade() -> None:
    op.execute("ALTER TABLE pools DROP COLUMN IF EXISTS minute_history_start")
