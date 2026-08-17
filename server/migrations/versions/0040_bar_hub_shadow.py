"""bar_hub/bar_shadow 表（ST7 阶段 0 影子期，设计 14 v2 §2.5/§5）

- bar_hub：共享行情 hub 落库（影子期独立表，diff 验证通过后才改写 bar_1min——防口径错位毒化真相源，评审 F2）
- bar_shadow：direct 模式 runner 落库（影子对比的另一侧，R-BR20）

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0040"
down_revision: str = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_bar_table(name: str) -> None:
    op.create_table(
        name,
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric()),
        sa.Column("high", sa.Numeric()),
        sa.Column("low", sa.Numeric()),
        sa.Column("close", sa.Numeric()),
        sa.Column("volume", sa.Numeric()),
        sa.Column("amount", sa.Numeric(), server_default="0"),
        sa.Column("untrusted", sa.Boolean(), server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("symbol", "ts"),
        if_not_exists=True,
    )


def upgrade() -> None:
    _create_bar_table("bar_hub")
    _create_bar_table("bar_shadow")
    # 评审 S8：client_order_id 唯一（多 worker 重启同分钟撞 id → 成交归属错认）
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_order_log_client_oid ON order_log (client_order_id)")
    # ST7：md_mode 全局默认 direct（任务级 params.md_mode 覆盖）
    op.execute("INSERT INTO system_config (key, value) VALUES ('md_mode', 'direct') "
               "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    op.drop_table("bar_shadow")
    op.drop_table("bar_hub")
    op.execute("DROP INDEX IF EXISTS ux_order_log_client_oid")
