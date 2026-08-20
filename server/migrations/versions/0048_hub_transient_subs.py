"""hub 临时订阅表（三档详情页"看过即订阅"，2026-08-20 用户裁定：XTP 为主路径，腾讯降级兜底）。

详情页打开非池标的 → upsert 30min TTL → hub ≤30s 订阅生效 → latest_tick 有值
→ 前端 30s 轮询自动从腾讯快照切 hub 实时。过期即不可见=自动退订（订阅真相源仍是 DB，
不破 R-SUB 设计）。上限 100 只（写侧挤最旧），XTP 100 只 tick≈200/s 无压力。

Revision ID: 0048
Revises: 0047
Create Date: 2026-08-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0048"
down_revision: str = "0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("hub_transient_subs",
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("expire_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_index("idx_hub_transient_expire", "hub_transient_subs", ["expire_at"])


def downgrade() -> None:
    op.drop_table("hub_transient_subs")
