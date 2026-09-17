"""批33a：权限单源化——permission 表 user 维退役锁列。

用户裁定（2026-09-16）：权限只留用户组维度（user 覆盖层退役；生产复核
subject_type='user' 行=0——2026-09-17）；CHECK 锁值域=role 单值。
表仅几十行——单步式直接 ADD（微秒级排他锁窗口归零，方案盲审 A-P2-5 择 a；
NOT VALID 两步式防锁收益为零反留 VALIDATE 窗自愈复杂度）。

Downgrade=DROP CHECK 对称（不恢复 user 行——本就为空；如需恢复写路径须手动
downgrade 后由旧版本代码接管）。effect='deny' 列不动（role 层 market_op 仍在用）。
Revision ID: 0082
Revises: 0081
"""
from alembic import op

revision = "0082"
down_revision = "0081"


def upgrade() -> None:
    op.execute("DELETE FROM permission WHERE subject_type='user'")   # 幂等保险（生产已零行）
    op.create_check_constraint("ck_permission_subject_role", "permission",
                               "subject_type='role'")


def downgrade() -> None:
    op.drop_constraint("ck_permission_subject_role", "permission", type_="check")
