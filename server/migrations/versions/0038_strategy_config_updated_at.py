"""strategy_config 补 updated_at（schema 漂移修复，2026-08-17 实盘测试发现）

根因：strategy_config 是前 alembic 时代运行时 DDL 建的表（0027 只收编了 10 张运行时表，不含它），
服务器上的旧形状无 updated_at 列，而 update_strategy 的 UPDATE 引用它 → UndefinedColumn 500。
本地库从新版运行时 DDL 建（含该列），故本地不复现。
幂等：ADD COLUMN IF NOT EXISTS，本地/新库 no-op。

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0038"
down_revision: str = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_config ADD COLUMN IF NOT EXISTS updated_at "
        "timestamptz DEFAULT now()"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_config DROP COLUMN IF EXISTS updated_at")
