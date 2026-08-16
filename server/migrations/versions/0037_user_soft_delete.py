"""users 加 deleted_at（软删除/注销，批次D，参考方案借鉴 2026-08-15）

删除语义：软删（deleted_at 置时间 + email/昵称/头像脱敏 + username 加后缀释放占用）；
审计/操作数据保留。登录与列表过滤已注销。
自助注销（个人中心）与 admin 删除共用；自助注销路径下末位 admin 保护真实可达（管理页路径不可达，
见 guard_user_mutation 注释），故 deactivate 端点单独设防。

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0037"
down_revision: str = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("users", "deleted_at")
