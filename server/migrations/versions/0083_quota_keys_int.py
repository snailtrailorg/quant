"""批36b-α：user_bot_quota/platform_bot_quota 两键 value_type 修正 string→int。

0078 seed 插入时未带 value_type（server_default='string'）——数值语义键挂在 string 型上：
更新端点 cast 梯子只认 int/float（不 cast 落字符串），消费点 im_bots int() 脏值直接炸
（盲审 B-P2-2③）。本迁移归位 int（值已数字字符串，零数据搬迁）。

Downgrade 对称回 'string'（回滚后旧代码按原状工作）。
Revision ID: 0083
Revises: 0082
"""
from alembic import op

revision = "0083"
down_revision = "0082"


def upgrade() -> None:
    op.execute("UPDATE system_config SET value_type='int' "
               "WHERE key IN ('user_bot_quota', 'platform_bot_quota')")


def downgrade() -> None:
    op.execute("UPDATE system_config SET value_type='string' "
               "WHERE key IN ('user_bot_quota', 'platform_bot_quota')")
