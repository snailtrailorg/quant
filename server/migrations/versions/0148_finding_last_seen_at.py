# EXPAND-CONTRACT: phase=expand（新增列+回填，无破坏——同 0146/0147 惯例）
"""批 124：security_finding 加 last_seen_at（时间戳精度）——同日复跑重判。

问题（2026-10-15 实证）：`last_seen` 是 date（日粒度）——同日多次扫描时，
"本轮不再报"的 finding 其 last_seen 仍等于今日 ⇒ fixed 判据（last_seen < 今日）
不成立 ⇒ 已修复项当日仍显示 active（prod 实测：setuptools 升级后 3 条 CVE 当日
未沉底，要等次日）。

方案：加 last_seen_at timestamptz，写入用 clock_timestamp()（真实时刻，
晚于事务开始）——快照 created_at 用 now()（事务开始时刻，早）⇒ 判据
`last_seen_at < snapshot.created_at` 可精确区分「本轮报的」与「本轮未报的」。

历史行回填：last_seen::timestamptz（date→当日 00:00 UTC，必早于任何本轮 created_at
⇒ 存量正确显示；日粒度语义不变）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0148"
down_revision: Union[str, Sequence[str], None] = "0147"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE security_finding ADD COLUMN IF NOT EXISTS last_seen_at timestamptz")
    op.execute(
        "UPDATE security_finding SET last_seen_at = last_seen::timestamptz "
        "WHERE last_seen_at IS NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE security_finding DROP COLUMN IF EXISTS last_seen_at")
