"""二档池数据增量游标表（U 审项 10，2026-08-20）

现状：每 300s 全量拉全部历史——小池无害，>50 标的撞 Tushare 限速。
方案：财务四表（income/balancesheet/cashflow/fina_indicator；dividend 窗口过滤
实测无效不增量）按公告日窗口增量拉取，窗口起点 = 表级游标 last_pull_date。
二档池成员驱动不进 sync_config（17 号裁定），游标独立建表。

游标语义：拉取窗口 [last_pull_date, today]（含起点重叠，幂等防漏）；
推进条件 = 该表本轮覆盖全部池标的。

附带：sync_log (sync_id, ts) 索引——progress 端点按此查询，量涨后免全表扫。

Revision ID: 0047
Revises: 0046
Create Date: 2026-08-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0047"
down_revision: str = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("pool_data_cursor",
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("last_pull_date", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("table_name"),
    )
    op.create_index("idx_sync_log_id_ts", "sync_log", ["sync_id", sa.text("ts DESC")])


def downgrade() -> None:
    op.drop_index("idx_sync_log_id_ts", table_name="sync_log")
    op.drop_table("pool_data_cursor")
