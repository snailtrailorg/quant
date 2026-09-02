"""批 7.1 · 告警订阅多目标（2026-09-02 用户裁定：每通道单目标是错的——多邮箱/多手机/多 bot）。

- 撤 UNIQUE(channel)，改 UNIQUE(channel, target)
- 存量单行数据零迁移（约束放宽即可）

Revision ID: 0062
Revises: 0061
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0062"
down_revision: str = "0061"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE alert_channel_sub DROP CONSTRAINT IF EXISTS uq_alert_channel")
    op.execute("ALTER TABLE alert_channel_sub ADD CONSTRAINT uq_alert_channel_target "
               "UNIQUE (channel, target)")


def downgrade() -> None:
    op.execute("ALTER TABLE alert_channel_sub DROP CONSTRAINT IF EXISTS uq_alert_channel_target")
    op.execute("ALTER TABLE alert_channel_sub ADD CONSTRAINT uq_alert_channel UNIQUE (channel)")
