"""批175 L3（contract）：position_snapshot 禁止 .SSE 落库（DB 硬闸）。

- **contract 型**：0181（expand）已把存量归一；本迁移加 CHECK 防回归。
- **单独一次 release**（前置：0 行 `%.SSE` + 所有 live-task 已用 175b 代码重启 + 过 ≥1 个 60s 快照周期）。
- `NOT VALID → VALIDATE` 两步：先跳过存量校验（短锁），再在线校验。
- downgrade **DROP CONSTRAINT**（禁 no-op——否则 rollback 回旧代码后每次快照写批违约束被吞，静默永久停更；往返脚本也会假绿）。

Revision ID: 0182
Revises: 0181
"""
from alembic import op
import sqlalchemy as sa

revision = "0182"
down_revision = "0181"

_CK = "ck_position_snapshot_no_sse"


def upgrade() -> None:
    # **前置断言**（审官 B P0-2）：存量仍有 .SSE ⇒ 直接 abort 阶段 5，
    # 不留给 ADD CONSTRAINT 后在 VALIDATE/后续写批里"静默冻结"。
    n = op.get_bind().execute(
        sa.text("SELECT count(*) FROM position_snapshot WHERE symbol LIKE '%.SSE'")
    ).scalar()
    if n:
        raise RuntimeError(
            f"0182 contract 前置失败：position_snapshot 仍有 {n} 行 .SSE——"
            "先确认 0181 已上产且所有 live-task 已用 175b 代码重启"
        )
    op.execute(
        f"ALTER TABLE position_snapshot ADD CONSTRAINT {_CK} "
        f"CHECK (symbol NOT LIKE '%.SSE') NOT VALID"
    )
    # VALIDATE 单独提交（env.py 的 begin_transaction 包整次 run_migrations；
    # 否则 ACCESS EXCLUSIVE 会从 ADD 一直持到 VALIDATE 结束，非"短锁"）
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TABLE position_snapshot VALIDATE CONSTRAINT {_CK}")


def downgrade() -> None:
    op.execute(f"ALTER TABLE position_snapshot DROP CONSTRAINT IF EXISTS {_CK}")
