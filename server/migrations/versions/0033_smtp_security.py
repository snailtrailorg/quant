"""smtp_security 加密方式选项（auto/ssl/starttls，默认 auto 按端口推断 RFC 8314）

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-14
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0033"
down_revision: str = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description) "
        "VALUES ('smtp_security', 'auto', 'text', 'SMTP 加密方式：auto=按端口推断(465→SSL,587→STARTTLS) / ssl / starttls') "
        "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    op.execute("DELETE FROM system_config WHERE key='smtp_security'")
