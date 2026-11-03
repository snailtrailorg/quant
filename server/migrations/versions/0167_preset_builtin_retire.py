"""screen_preset builtin 值退役（批 165——用户裁定：预置=普通方案，可删可改无特权）。

source 列仅展示标签（形状判别单源不受影响）；builtin→builder 后 CHECK 约束仍含
builtin 值但不再写入（历史兼容保留，收紧留给未来清理批）。

Revision ID: 0167
Revises: 0166
"""
from alembic import op

revision = "0167"
down_revision = "0166"


def upgrade():
    op.execute("UPDATE screen_preset SET source='builder' WHERE source='builtin'")


def downgrade():
    op.execute("UPDATE screen_preset SET source='builtin' WHERE source='builder' AND created_by IS NULL")
