"""批13 五轮：IM 全面用户化——admin 全局面删除，存量平台 bot（owner NULL）归 admin 用户。

Downgrade 可逆（恢复 owner=NULL）。归 admin 后：消息=owner 直通、告警下拉可选、
个人中心可见可管理（admin 也是用户）。

Revision ID: 0073
Revises: 0072
"""
from alembic import op
import sqlalchemy as sa

revision = "0073"
down_revision = "0072"


def upgrade() -> None:
    # admin=首个 builtin admin 用户（id 1 为种子 admin；保守取 username='admin'）
    op.execute("""
        UPDATE im_bot_config b
        SET owner_user_id = (SELECT id FROM users WHERE username='admin' ORDER BY id LIMIT 1)
        WHERE owner_user_id IS NULL
    """)


def downgrade() -> None:
    op.execute("UPDATE im_bot_config SET owner_user_id = NULL WHERE owner_user_id IS NOT NULL")
