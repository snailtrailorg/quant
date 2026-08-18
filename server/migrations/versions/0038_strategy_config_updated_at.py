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
    # 2026-08-18 #48 scratch 跑链实锤：本迁移假设表已存在（前 alembic 遗留），fresh run 到此必炸
    # （表要到 0042 才被收编创建，且 0042 的建表已含 updated_at）。加表存在守卫：
    # 已应用环境（表在）行为不变；fresh run 跳过本条，由 0042 建全形。
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = current_schema() AND table_name = 'strategy_config')
            THEN
                ALTER TABLE strategy_config ADD COLUMN IF NOT EXISTS updated_at
                    timestamptz DEFAULT now();
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_config DROP COLUMN IF EXISTS updated_at")
