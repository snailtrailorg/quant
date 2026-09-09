"""user_group 表（批11B 用户组动态化：四角色硬编码 → DB 用户组实体）。

seed 四内置组（builtin=true 锁名不可删不可改名，可改描述/权限——用户裁定①）。
join 键=组名字符串：users.role / permission.subject_id 不加 FK（require_perm 零改动）。
upgrade 含一致性巡检（permission 表 role 维出现四内置名之外的 subject_id → log warning
不阻断——0071 前的运行期残留显性化，双盲审 B P2-2）。
非破坏 DDL（CREATE TABLE+INSERT），无部署特殊动作（不走 allow_contract）。

Revision ID: 0071
Revises: 0070
Create Date: 2026-09-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0071"
down_revision: str = "0070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BUILTIN = [
    ("admin", "管理员：全权限（锁键 user_mgmt/resume/account_keys 恒独占）"),
    ("trader", "交易员：策略启停/熔断/下单/实盘开关"),
    ("analyst", "研究员：策略/回测/数据同步"),
    ("viewer", "观察者：只读"),
]


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS user_group (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        description TEXT,
        builtin BOOLEAN NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""")
    # 幂等 seed：不覆盖已改的 description（ON CONFLICT DO NOTHING）
    for name, desc in _BUILTIN:
        op.execute(sa.text(
            "INSERT INTO user_group (name, description, builtin) VALUES (:n, :d, true) "
            "ON CONFLICT (name) DO NOTHING").bindparams(n=name, d=desc))
    # 一致性巡检（不阻断）：permission.role 维的幽灵组名显性化
    op.execute(sa.text("""
        DO $$
        DECLARE ghost TEXT;
        BEGIN
            FOR ghost IN
                SELECT DISTINCT p.subject_id FROM permission p
                LEFT JOIN user_group g ON g.name = p.subject_id
                WHERE p.subject_type='role' AND g.id IS NULL
            LOOP
                RAISE WARNING 'permission 表存在 user_group 无对应组的角色行: %（批11B 巡检）', ghost;
            END LOOP;
        END $$"""))


def downgrade() -> None:
    # 自定义组数据不回收（permission/users.role 中的自定义组名残留由应用层零权限兜底）
    op.execute("DROP TABLE IF EXISTS user_group")
