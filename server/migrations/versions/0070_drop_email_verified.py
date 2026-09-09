"""users 删 email_verified 列（批11 用户裁定③：连库列一起删）。

邮箱验证流程从未启用（邀请制开通即置 true，无独立验证链路），列纯冗余。
downgrade 重建列（default false）——数据不可逆，但该列本无信息量。

Revision ID: 0070
Revises: 0069
Create Date: 2026-09-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0070"
down_revision: str = "0069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("users", "email_verified")


def downgrade() -> None:
    op.add_column("users", sa.Column("email_verified", sa.Boolean(),
                                     server_default=sa.text("false")))
