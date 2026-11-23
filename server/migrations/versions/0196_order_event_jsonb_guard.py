"""0196：order_event.detail 加 jsonb 类型守卫（补 0195 遗漏）。

0195 建表时 detail jsonb 未加类型守卫 CHECK，违反 D18 全仓 jsonb 列守卫律
（test_jsonb_columns_guarded 闸门会红）。本迁移补 object-only 守卫。
detail 写入仅经 record_order_event 的 jsonb()（object 或 NULL），故存量无损坏行。

Revision ID: 0196
Revises: 0195
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0196"
down_revision: Union[str, Sequence[str], None] = "0195"
branch_labels: Union[str, Sequence[str], str, None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_order_event_detail_object"


def upgrade() -> None:
    # 先证无损坏行（有则中止，不静默加约束）
    bad = op.get_bind().execute(
        "SELECT id FROM order_event WHERE detail IS NOT NULL "
        "AND jsonb_typeof(detail) <> 'object' LIMIT 5").fetchall()
    if bad:
        raise RuntimeError(f"0196 中止：order_event.detail 有非 object 行 {[r[0] for r in bad]}")
    op.execute(
        f"ALTER TABLE order_event ADD CONSTRAINT {_CONSTRAINT} "
        f"CHECK (detail IS NULL OR jsonb_typeof(detail) = 'object')")


def downgrade() -> None:
    op.execute(f"ALTER TABLE order_event DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
