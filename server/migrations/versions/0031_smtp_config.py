"""SMTP 邮件配置 DB 化（system_config，前端「系统配置」页可改）

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-14

5 个 key：smtp_host / smtp_port / smtp_username / smtp_password(password 型加密) / smtp_from。
读取优先级（email_service._smtp_config）：DB 非空 > .env SMTP_* > 都无走 DEV 打印。
密码加密存（crypto_utils Fernet，同 broker_config），API 永不回传明文，留空=不修改。
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0031"
down_revision: str = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEEDS = [
    ("smtp_host", "", "text", "SMTP 服务器（如 smtpdm.aliyun.com / smtp.qq.com）；留空用 .env"),
    ("smtp_port", "", "text", "SMTP 端口（587 STARTTLS）；留空用 .env"),
    ("smtp_username", "", "text", "SMTP 用户名（发信账号）；留空用 .env"),
    ("smtp_password", "", "password", "SMTP 密码/授权码（加密存储，不回显）；留空=不修改"),
    ("smtp_from", "", "text", "发件人地址（须与服务商一致）；留空用 .env"),
]


def upgrade() -> None:
    for key, value, vtype, desc in _SEEDS:
        op.execute(
            "INSERT INTO system_config (key, value, value_type, description) "
            f"VALUES ('{key}', '{value}', '{vtype}', '{desc}') ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    for key, _, _, _ in _SEEDS:
        op.execute(f"DELETE FROM system_config WHERE key='{key}'")
