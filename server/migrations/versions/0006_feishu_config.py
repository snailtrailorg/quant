"""feishu_config 表（飞书机器人配置，DB 化 + 扫码接入）

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-03

- feishu_config 表：飞书机器人凭证（register_app 扫码返回 client_id/secret），加密
- 弃 .env LARK_*，配置管理 DB 化
- 长连接模式（lark.ws.Client），不用 webhook（verification_token/encrypt_key 长连接可不用，保留兼容）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feishu_config",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("app_id", sa.Text(), nullable=False),                       # register_app 返回的 client_id
        sa.Column("app_secret_encrypted", sa.Text(), nullable=False),          # Fernet 加密
        sa.Column("verification_token_encrypted", sa.Text(), nullable=True),   # 事件订阅（长连接可不用，保留）
        sa.Column("encrypt_key_encrypted", sa.Text(), nullable=True),
        sa.Column("event_types", sa.Text(), nullable=True),                    # 订阅事件类型，逗号分隔
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    # 单机器人：最新一条为当前配置（不种子，扫码后填）


def downgrade() -> None:
    op.drop_table("feishu_config")
