# EXPAND-CONTRACT: phase=contract pair=0027
"""批 142：DROP alert_history（僵尸表清理，批 129 挂账收口）。

背景：alert_history（告警历史，0027 建表）0 行、生产代码零读点零写点
（批 129 全仓盘点实证；告警实走 notification 链）；本批按两步走 contract 删表。
pair=0027（expand）为回溯声明——0027 即建表迁移本身，声明随批 142 A 段
（a3d49dd）先行上产，本 contract 是其收尾。

回滚：downgrade 按 0027 形态重建六列（id/ts/level/title/body/channel）——
语义=结构可回滚、不 resurrect 历史数据（表删时 0 行，无数据面损失）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0161"
down_revision: Union[str, Sequence[str], None] = "0160"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("alert_history")


def downgrade() -> None:
    op.create_table(
        "alert_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("level", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("body", sa.Text()),
        sa.Column("channel", sa.Text()),
    )
