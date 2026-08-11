"""channel_config 表（消息通道配置 DB 化，平台化消息层 PT4）。

MessageChannel 接口抽象：别人实现 MessageChannel 接入自己的渠道（钉钉/邮件等）。
当前实现 WechatWork/Discord/ServerChan（包装现有 alert_notify 渠道）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "channel_config",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),          # wechat_work/discord/serverchan/feishu/...
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text()),              # Fernet 加密（webhook_url/sckey）
        sa.Column("params", sa.Text()),                             # JSON: 额外参数
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_channel_provider", "channel_config", ["provider"])


def downgrade():
    op.drop_index("ix_channel_provider", table_name="channel_config")
    op.drop_table("channel_config")
