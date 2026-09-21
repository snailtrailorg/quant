"""批 58b：竞价条并入首根——bar_minute dataset_version 1→2（数据变更批，29 号 §六 验收 2）。

竞价条并入改变分钟线数据语义（09:30 并入 09:31），bar_1min/bar_5min 表级版本从 1 推到 2：
新写入行走 DEFAULT 2（血缘可查，行级 dataset_version 区分新旧）；存量行由
scripts/merge_auction_into_first.py 一次性迁移更新（09:30 并入 09:31 + dataset_version=2）。
ALTER COLUMN SET DEFAULT 是元数据操作（不重写表），expand-only，DDL 门放行。

Revision ID: 0095
Revises: 0094
"""
from alembic import op

revision = "0095"
down_revision = "0094"


def upgrade() -> None:
    for tbl in ("bar_1min", "bar_5min"):
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN dataset_version SET DEFAULT 2")


def downgrade() -> None:
    for tbl in ("bar_1min", "bar_5min"):
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN dataset_version SET DEFAULT 1")
