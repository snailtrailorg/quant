"""feishu_config 加 role + lang + description（per-机器人系统角色+语言+备注）

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-04

- role：系统 RBAC 角色（admin/trader/analyst/viewer，默认 viewer）。机器人=登录账号，有权限级别，超出权限的操作被拒
- lang：语言偏好（zh/en，默认 null=浏览器语言）。机器人回复用此语言，修改后生效
- description：备注描述
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("feishu_config", sa.Column("role", sa.Text(), nullable=False, server_default="viewer"))
    op.add_column("feishu_config", sa.Column("lang", sa.Text(), nullable=True))
    op.add_column("feishu_config", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("feishu_config", "description")
    op.drop_column("feishu_config", "lang")
    op.drop_column("feishu_config", "role")
