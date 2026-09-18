"""批53 追加：SMTP/短信通道加 vendor（运营商——用户自输入显示名）。

用户裁定（2026-09-18）：运营商列=用户自己输入（编辑弹窗有行/库有配置项）——
SMTP 原无此列（批53 曾误用 host 充当已撤）；SMS provider=技术实现列（CK 锁 aliyun，
发送链依赖）不动，vendor=显示名独立列（空回落 provider 映射）。
Revision ID: 0089
Revises: 0088
"""
from alembic import op
import sqlalchemy as sa

revision = "0089"
down_revision = "0088"


def upgrade() -> None:
    op.add_column("smtp_provider", sa.Column("vendor", sa.Text(), nullable=False, server_default=sa.text("''")))
    op.add_column("sms_provider", sa.Column("vendor", sa.Text(), nullable=False, server_default=sa.text("''")))


def downgrade() -> None:
    op.drop_column("smtp_provider", "vendor")
    op.drop_column("sms_provider", "vendor")
