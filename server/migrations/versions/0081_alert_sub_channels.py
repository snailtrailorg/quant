"""批34：告警订阅通道级选择。

alert_user_sub ADD channels JSONB NULL——三态（用户裁定 2026-09-17）：
  NULL = 全通道自动（存量行为，此后新增通道自动纳入；存量行零迁移）
  非空 = 按勾选发（["email","sms","im:3"]——im 粒度到 bot）
  []   = 零通道静音（行保留占位供以后增加——用户裁定"行永不静默删除"）
失效勾选（通道实体已删）由读取面剥离+保存面懒清理，DB 不做联动。

ADD COLUMN 无 default=PG18 瞬时锁无 rewrite（表 ≤10 行）。Downgrade drop_column 对称。
Revision ID: 0081
Revises: 0080
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0081"
down_revision = "0080"


def upgrade() -> None:
    op.add_column("alert_user_sub", sa.Column("channels", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("alert_user_sub", "channels")
