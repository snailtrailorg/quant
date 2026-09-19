"""批55b：account_keys 权限键退役（API 密钥管理收编进外部接口统一表管理）。

背景：/api/account 五端点门随 55b 统一（GET=read，写=system_config——与集成中心页签门
一致），防漂移闸抓到 account_keys 零绑定（批33b 注册表闸按设计工作）。键从
API_PERM_KEYS/LOCKED_PERM_KEYS/permGroups/词条同步退役；本迁移清存量组绑定行。

Revision ID: 0091
Revises: 0090
"""
from alembic import op

revision = "0091"
down_revision = "0090"


def upgrade() -> None:
    op.execute("DELETE FROM permission WHERE resource = 'account_keys'")


def downgrade() -> None:
    # admin 组恢复绑定（其余组不重建——键已退役，downgrade 仅保 admin 完整性）
    op.execute("""
        INSERT INTO permission (subject_id, subject_type, resource)
        SELECT 'admin', 'role', 'account_keys'
        WHERE NOT EXISTS (SELECT 1 FROM permission
                          WHERE subject_id='admin' AND subject_type='role'
                            AND resource='account_keys')
    """)
