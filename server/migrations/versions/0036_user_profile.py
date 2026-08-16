"""users 加 nickname / avatar_url / avatar_updated_at（批次C 用户表象，参考方案借鉴 2026-08-15）

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0036"
down_revision: str = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("nickname", sa.Text()))
    op.add_column("users", sa.Column("avatar_url", sa.Text()))
    op.add_column("users", sa.Column("avatar_updated_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("users", "avatar_updated_at")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "nickname")
