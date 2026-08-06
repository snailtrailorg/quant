"""add email to users + user_tokens 表

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-02

- users 加 email + email_verified（邀请制用户管理）
- user_tokens 表（invite/password_reset/email_verify token）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users 加 email + email_verified
    op.add_column("users", sa.Column("email", sa.Text(), unique=True))
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false")))

    # 2. user_tokens 表（邀请/重置/验证 token）
    op.create_table(
        "user_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),  # invite 时还没用户，可空
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False, unique=True),
        sa.Column("type", sa.Text(), nullable=False),  # invite / password_reset / email_verify
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), server_default=sa.text("false")),
    )
    op.create_index("idx_user_tokens_token", "user_tokens", ["token"])
    op.create_index("idx_user_tokens_email_type", "user_tokens", ["email", "type"])


def downgrade() -> None:
    op.drop_index("idx_user_tokens_email_type", "user_tokens")
    op.drop_index("idx_user_tokens_token", "user_tokens")
    op.drop_table("user_tokens")
    op.drop_column("users", "email_verified")
    op.drop_column("users", "email")
