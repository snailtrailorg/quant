"""批 6b：md_mode direct 残留清零（direct 退役，防误配 EX_CONFIG）。

direct 主循环 2026-09-01 批 6b 退役后，strategy_runner.main 对 md_mode=direct
显式 EX_CONFIG(78) fail-fast——system_config 全局键若残留 direct，所有无
params.md_mode 的任务启动即拒。本迁移幂等清零（代码盲审 B-P1：本地 dev DB
实测残留 direct；产线经本迁移随管道自动同治）。

Revision ID: 0058
Revises: 0057
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0058"
down_revision: str = "0057"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None


def upgrade() -> None:
    op.execute("UPDATE system_config SET value='hub' WHERE key='md_mode' AND value='direct'")


def downgrade() -> None:
    # 不回写 direct：回滚场景走旧代码+旧配置形态，且 direct 已无代码路径
    pass
