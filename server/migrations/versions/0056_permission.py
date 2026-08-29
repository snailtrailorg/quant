"""permission 权限表（P3-7，web-design 10 §3/§6 阶段 A+B）。

- 阶段 A：require_perm 底层从硬编码 PERMISSIONS 字典换查表（接口不变，行为兜底不变——
  表空/读失败回退字典值，改权限不再发版）
- 阶段 B：四角色默认矩阵数据化 seed（= 现字典值逐条导入，上线零行为变化）
- 用户级覆盖（subject_type=user）阶段 C/D 再用，表结构一步到位。

Revision ID: 0056
Revises: 0055
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0056"
down_revision: str = "0055"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None

# 现字典逐条（auth.py PERMISSIONS）——seed 后行为零变化
_ROLE_PERMS = {
    "viewer": {"read"},
    "analyst": {"read", "strategy_control", "data_sync", "system_config"},
    "trader": {"read", "strategy_control", "halt", "trade", "live_trading_control"},
    "admin": {"read", "strategy_control", "data_sync", "halt", "resume", "trade",
              "live_trading_control", "risk_rules", "account_keys", "user_mgmt",
              "system_config", "llm_config", "im_bots_config"},
}


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS permission (
        id BIGSERIAL PRIMARY KEY,
        subject_type TEXT NOT NULL,        -- role | user（用户覆盖属阶段 C）
        subject_id TEXT NOT NULL,          -- 角色名 | user_id
        dimension TEXT NOT NULL DEFAULT 'api',   -- nav | api | data（本批只落 api）
        resource TEXT NOT NULL,            -- 权限键（read/strategy_control/…）
        effect TEXT NOT NULL DEFAULT 'allow',    -- allow | deny（deny 优先，10 §1）
        note TEXT,
        updated_by TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (subject_type, subject_id, dimension, resource, effect)
    )""")
    for role, perms in _ROLE_PERMS.items():
        for p in perms:
            op.execute(
                "INSERT INTO permission (subject_type, subject_id, dimension, resource, effect, note) "
                "VALUES ('role', %s, 'api', %s, 'allow', 'P3-7 seed') "
                "ON CONFLICT DO NOTHING", (role, p))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS permission")
