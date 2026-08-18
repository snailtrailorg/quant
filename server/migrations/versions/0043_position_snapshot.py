"""ST2 持仓真相源（消 D2 订单账实分离；N 审 v2 设计）

- position_snapshot：**当前状态表**（非流水）——每批同事务 DELETE 该账户旧行 + INSERT 当前批。
  N-F1（空批不可表示）：清仓回报 0 行 → DELETE 后表空，天然表示空仓；N-量级：行数=常数
  （每账户标的数），保留期任务连带消解。
- position_refresh：批次心跳（account_id 主键，ts/rows/task_id）——区分"从未查过/停更"（陈旧）
  与"空仓"（N-S5：unknown ≠ flat）；/api/position 据此给 stale 语义。
- direction 列（N-S3）：XTP 映射含 NET/LONG/SHORT（两融 Short 行），如实写不过滤，端点读 Long。
- account_id 列（N-S4）：query_position 回报是全账户仓位（与任务标的无关）——按账户存真相，
  多任务同账户不再双份真相（表按 account_id 分区覆盖）。

Revision ID: 0043
Revises: 0042
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0043"
down_revision: str = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "position_snapshot",
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False, server_default="long"),
        sa.Column("volume", sa.Numeric(), nullable=False),
        sa.Column("frozen", sa.Numeric(), server_default="0"),
        sa.Column("cost_price", sa.Numeric()),
        sa.Column("pnl", sa.Numeric()),
        sa.Column("yd_volume", sa.Numeric(), server_default="0"),
        sa.Column("task_id", sa.Text()),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("account_id", "symbol", "direction"),
    )
    op.create_table(
        "position_refresh",
        sa.Column("account_id", sa.Text(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("task_id", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("position_refresh")
    op.drop_table("position_snapshot")
