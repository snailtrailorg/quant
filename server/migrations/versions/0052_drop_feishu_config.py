"""IM 统一接入批 2 收尾:DROP feishu_config(19 号 v2 §5——批 2 切完全部读路径)。

批 2 读写点全清单已切换(19 号 v2):FeishuClient/ws_client/web 端点(新 /api/im-bots)/
扫码 tasks/签名/授权/schema_expectations。旧表数据已由 0051 迁入 im_bot_config。

Revision ID: 0052
Revises: 0051
Create Date: 2026-08-21
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0052"
down_revision: str = "0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 不可逆数据迁移:0051 时 feishu_config 是源表;此刻起 im_bot_config 是唯一真相源
    op.drop_table("feishu_config")


def downgrade() -> None:
    # 结构还原(数据不回填——19 号 v2 §5:downgrade 有损已声明)
    import sqlalchemy as sa
    op.create_table("feishu_config",
        sa.Column("id", sa.Integer(), autoincrement=True),
        sa.Column("app_id", sa.Text()),
        sa.Column("app_secret_encrypted", sa.Text()),
        sa.Column("verification_token_encrypted", sa.Text()),
        sa.Column("encrypt_key_encrypted", sa.Text()),
        sa.Column("event_types", sa.Text()),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("name", sa.Text()),
        sa.Column("role", sa.Text(), server_default="viewer"),
        sa.Column("description", sa.Text()),
        sa.PrimaryKeyConstraint("id"),
    )
