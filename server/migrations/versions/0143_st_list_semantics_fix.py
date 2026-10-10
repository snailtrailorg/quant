# EXPAND-CONTRACT: phase=contract pair=0142
"""批 117 P0-2 纠偏：st_list_sync 的语义修正（数据迁移，零 DDL）。

批 111 两步走声明说明：本迁移是 0142 的 contract 步——**但无任何破坏性 op**
（无 DROP/RENAME/ALTER），只做两件数据修正：

1. **description 纠偏**：0142 种子行 description 写着「perms 无档改 fail-closed
   （表空=冷启动保护期例外）」——这是已被 prod 实战否定的错误判据（P0-2：
   stock_st 是 ST-only 单态名单，无档=非 ST 放行；判据立法见 decisions.md
   2026-10-10「权限判定层不得依赖数据同步模块的生命周期」）。假话不能留在
   配置里误导后续运维。
2. **retention 补地板**：0142 种子 `retention=NULL` ⇒ 批 108「F-1 首跑即全史」
   规则下首跑从 2010 年逐日拉 16 年（dev 实测 4069 日 50 万行、撞
   SoftTimeLimit）。ST 判定只需**近期**快照——补 `retention='2026-01-01'`
   （半年窗口，足以覆盖判定所需新鲜度；历史段需要时走显式 backfill）。
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0143"
down_revision: Union[str, Sequence[str], None] = "0142"


def upgrade() -> None:
    op.execute(
        "UPDATE sync_config SET description = "
        "'全市场ST/风险警示名单快照（stock_st，~200行/日，ST-only 单态名单）。"
        "批117起ST判定真源；perms 只消费正向事实：在档=ST 判、不在档=非 ST 放行"
        "（P0-2 语义修正，见 decisions.md 2026-10-10 架构律）', "
        "retention = '2026-01-01' "
        "WHERE id = 'st_list_sync'")


def downgrade() -> None:
    # description 回原文（历史考据见 git）；retention 回 NULL（0142 原状）
    op.execute(
        "UPDATE sync_config SET description = "
        "'全市场ST/风险警示名单快照（stock_st，~200行/日）。批117起ST判定真源"
        "（原namechange派生降级历史参考）；perms无档fail-closed依赖本表非空', "
        "retention = NULL "
        "WHERE id = 'st_list_sync'")
