# EXPAND-CONTRACT: phase=expand pair=None  # 数据清理（无表结构变更）——expand-only，无 contract 半部
"""批 171f（DEF-15）：sync_gap 孤儿 open 态行一次性清理（数据卫生+防再生闸）。

任务＝`flow/任务/批171f-DEF15孤儿清理与DEF12返回值.md`（v2，审官「可编码」）。

- 口径：只删 `state='open'` 且 sync_id 不在 sync_config 的行（open=活缺口语义，孤儿无
  handler 永不自愈⇒永久假活；closed/unreachable/policy_discard/uncertain 保留——历史
  审计链+保守删最少）。审官实核：当前读点均按活 sync_id 参数化，本清理是卫生非读修复。
- 幂等：重跑零行。
- **downgrade＝no-op**：删的是孤儿行，无恢复语义（closed 态行从未被删）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0172"
down_revision: Union[str, Sequence[str], None] = "0171"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM sync_gap WHERE state='open' "
        "AND sync_id NOT IN (SELECT id FROM sync_config)")


def downgrade() -> None:
    # no-op：孤儿 open 行无恢复语义（有意删除，非迁移事故）。
    pass
