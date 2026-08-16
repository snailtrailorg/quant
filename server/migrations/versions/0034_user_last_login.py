"""users 加 last_login_at / last_login_ip（登录审计，借鉴用户管理参考方案 2026-08-15）

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034"
down_revision: str = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("last_login_ip", sa.Text()))


def downgrade() -> None:
    op.drop_column("users", "last_login_ip")
    op.drop_column("users", "last_login_at")
