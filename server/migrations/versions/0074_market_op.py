"""市场操作权限（批15：market_op 维 + live_task.owner_username + 内置组表补齐）。

- live_task + owner_username TEXT NULL FK→users.username（operator 全链=username，
  与 permission.user 维 subject_id 同 join 键，零转换——双盲审 P0-1 修正）
- 存量回填：子查询取首个活跃 admin（多环境可重放，不硬编码 id——盲审 P1-3）
- permission seed：admin/trader × 5 市场 allow（10 行）；analyst/viewer 零行=自动全拒
  （无行=deny 的 fail-closed 缺省，2026-09-11 用户裁定）
- 越权修正：DELETE analyst 的 api/system_config 行（0056 seed 遗留——W5 只删了
  PERMISSIONS 字典键没删表行，而"表有行全量以表为准"→产线 analyst 实际持权至今）
- 垃圾清理：perm_test/quant_ops2/quant_ops 三组（调试遗留；DELETE 加"无用户挂靠"
  守卫——users.role 无外键但语义引用组名，防孤儿化在线用户）

Revision ID: 0074
Revises: 0073
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0074"
down_revision: str = "0073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MARKETS = ("convertible", "etf", "astock", "binance_perp", "okx_perp")
_TRASH_GROUPS = ("perm_test", "quant_ops2", "quant_ops")


def upgrade() -> None:
    # ON UPDATE CASCADE（代码盲审 A-P1）：soft_delete_user 改名（{name}_deleted_{hex}）时
    # 级联跟随——无此子句名下有任务的用户删除/注销必 ForeignKeyViolation 500（本地实证）
    op.execute("ALTER TABLE live_task ADD COLUMN owner_username TEXT "
               "REFERENCES users(username) ON UPDATE CASCADE")
    # 存量回填（子查询取首个活跃 admin；执行后断言无残留——空 admin=环境异常应 fail）
    op.execute(
        "UPDATE live_task SET owner_username="
        "(SELECT username FROM users WHERE role='admin' AND enabled "
        " AND deleted_at IS NULL ORDER BY id LIMIT 1) "
        "WHERE owner_username IS NULL")
    # SQLAlchemy 2.x：bind.execute 收 text() 不收裸字符串
    _left = op.get_bind().execute(
        sa.text("SELECT count(*) FROM live_task WHERE owner_username IS NULL")).scalar()
    if _left:
        raise RuntimeError(f"live_task 回填后仍有 {_left} 行 owner 为空（无活跃 admin？）")

    # 内置组 market_op seed（仅 allow 行；analyst/viewer 零行=deny 缺省）
    for role in ("admin", "trader"):
        for m in _MARKETS:
            op.execute(
                "INSERT INTO permission (subject_type, subject_id, dimension, resource, effect, note) "
                f"VALUES ('role', '{role}', 'market_op', '{m}', 'allow', '批15 seed') "
                "ON CONFLICT DO NOTHING")

    # 越权修正（analyst 的 system_config——0056 seed 遗留；user 维显式 override 不动）
    op.execute(
        "DELETE FROM permission WHERE subject_type='role' AND subject_id='analyst' "
        "AND dimension='api' AND resource='system_config'")

    # 垃圾组清理（守卫：仅删无用户挂靠的组）
    op.execute(
        f"DELETE FROM user_group WHERE name IN {_TRASH_GROUPS} "
        "AND name NOT IN (SELECT DISTINCT role FROM users)")
    op.execute(
        f"DELETE FROM permission WHERE subject_id IN {_TRASH_GROUPS}")

    # data 维全退役（代码盲审 A-P2）：枚举已换、解析函数已删——存量行成"UI 可见无法清"死行
    op.execute("DELETE FROM permission WHERE dimension='data'")


def downgrade() -> None:
    # 版本回滚=配置回 seed 态：market_op 全清（不带 note 条件——UI 编辑过的行一并删，
    # 防"downgrade→upgrade"后同键 allow+deny 双行漂移，代码盲审 B-P2）；data 维行不恢复
    # （该维已无任何读者）。越权行/垃圾组不恢复（修正与清理不可逆）。
    op.execute("DELETE FROM permission WHERE dimension='market_op'")
    op.execute("ALTER TABLE live_task DROP COLUMN IF EXISTS owner_username")
