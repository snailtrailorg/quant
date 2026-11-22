"""批189-M7：health_event 告警查询复合索引（rule_id, component, ts DESC）。

活动/历史告警每 30s 按 (rule_id, component) 取最新行；0041 只有 ts 单列索引，
DISTINCT ON/LATERAL 会退化为全表排序+逐行查找。CONCURRENTLY 建（0060/0164 先例）。

Revision ID: 0193
Revises: 0192
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0193"
down_revision: Union[str, Sequence[str], None] = "0192"
branch_labels: Union[str, Sequence[str], str, None] = None
depends_on: Union[str, Sequence[str], None] = None

_IDX = "ix_health_event_rule_component_ts"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_IDX} "
            "ON health_event (rule_id, component, ts DESC)")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_IDX}")
