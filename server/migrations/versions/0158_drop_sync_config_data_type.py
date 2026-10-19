# EXPAND-CONTRACT: phase=contract pair=0154
"""批 129：DROP sync_config.data_type（僵尸列清理，批 127 Z4 挂账收口）。

背景：data_type（品类 tag，0001 建 NOT NULL）自批 127 Z4 起生产代码零读点零写点
（仅数据管理页直渲，随 0154 批已退役）；本批按两步走 contract 删除列。
pair=0154（expand，已于 202610182043-b5e8418 上产）含该列值归一（etf→listed_fund），
本 contract 是其收尾。

回滚：downgrade 重建列**可空**（0001 原 NOT NULL——恢复 NOT NULL 需默认值，
语义=结构可回滚、不 resurrect 历史值；旧代码自 0154 批起零读该列，行为面兼容）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0158"
down_revision: Union[str, Sequence[str], None] = "0157"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("sync_config", "data_type")


def downgrade() -> None:
    op.add_column("sync_config", sa.Column("data_type", sa.Text()))
