"""user_tokens 加 revoked（邀请撤销，借鉴用户管理参考方案 2026-08-15 批次B）

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0035"
down_revision: str = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_tokens", sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("user_tokens", "revoked")
