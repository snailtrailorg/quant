"""feishu_config 加 name 字段（多机器人支持）

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-04

- feishu_config 加 name（机器人名称），支持多机器人配置
- 多机器人：每行一个（id PK），每机器人独立长连接 systemd quant-feishu-bot@<id>
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("feishu_config", sa.Column("name", sa.Text(), nullable=True))
    # 已有行 name 设默认
    op.execute("UPDATE feishu_config SET name='飞书机器人' WHERE name IS NULL")


def downgrade() -> None:
    op.drop_column("feishu_config", "name")
