# EXPAND-CONTRACT: phase=offline-atomic reason="批189-M8 退役 accounts 空表（prod 实测 0 行、无消费者；随发布原子执行）" backup="GET /api/account 空表确认（0 行，无数据可备）" restore=rehearsed
"""批189-M8：退役 P4-5 accounts 表（API 密钥账户卡）。

依据：活链路绑定 trade_interface.code；accounts 仅被 /api/account 自读自写，
生产实测 0 行、无外键/触发器/视图消费者。凭证/参数已由 provider 模板按接口行承载。

Revision ID: 0194
Revises: 0193
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0194"
down_revision: Union[str, Sequence[str], None] = "0193"
branch_labels: Union[str, Sequence[str], str, None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("accounts")


def downgrade() -> None:
    # 仅恢复结构（原表生产为空；密钥列不再写入）
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text()),
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("api_key_enc", sa.Text()),
        sa.Column("api_secret_enc", sa.Text()),
        sa.Column("api_key_hint", sa.Text()),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
