"""批 109：对账输出闭环落表——`sync_gap`（派生视图，非第二真源）。

母设计：`flow/方案/同步窗口与参数分层-设计.md` §7.2（缺口去哪/谁重拉/谁告警）·
§5.3（封顶不得静默）· §5.4（uncertain 抑制主张）；方案＝`flow/任务/批109-分族对账与输出闭环.md` v3。

**为什么建这张表**：批 108 只把排除段（`policy_discard`/`unreachable`）**记日志**——对账若只产报告
无消费者＝等于没做（母设计双盲审① P1）。本表是对账结果的**唯一消费者接口**：per-date 内联重拉
仍缺、per-symbol 标的级差集、以及批 108 的排除段终态，全部落这里。

**它是派生视图，不是真源（设计 §7.2 明写）**：真源＝「数据表本身 ＋ 生命周期主档（SM）＋ 窗口边界
规则」。⇒ 可随时清空重算；**禁止**任何窗口/期望集计算读它（禁读闸＝`_FORBIDDEN_SYNC_GAP_READERS`
测试闸）。「长期停留 `open` 的行数」即存量缺口指标。

**锚键＝缺口起点，非整区间（步 2 双盲审 P0-1 修正，双同）**：`UNIQUE (sync_id, symbol, gap_start)`。
每轮对账按 scope 做**视图式全量重同步**（以本轮现算为唯一真源，与 scope 内非终态行做集合差）：
失配 ⇒ `closed`；对齐 ⇒ `UPDATE gap_end/last_seen`；新起点 ⇒ 重开或 INSERT。
⇒ 「缺口补了一半」自然产出新 `gap_start` 行，**不可能出现幽灵 open 或谎报 closed**。

**写侧纪律**：读-改-写（SELECT 现状 → 集合分类 → 逐行 UPDATE/INSERT），**不走 `ON CONFLICT` 盲合并**
——盲合并会把旧行的 `pull_count`/`last_seen` 带进无关的新缺口，污染「连续 N 轮同缺口」判据（批 110）。

**写法**：`CREATE TABLE IF NOT EXISTS`（仓例：16/17 建表迁移同款，最新 0127/0135 一致）——裸
`CREATE TABLE` 会让「部署中断强制复跑」炸 `DuplicateTable`，往返脚本的强制复跑用例当场抓红。
全部 `op.execute(<内联字面量>)`（`--sql` 离线渲染安全，同 0132~0139）。

**downgrade**：`DROP TABLE IF EXISTS`（破坏性但对称；本表是派生数据，无信息损失——可重建）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0140"
down_revision: Union[str, Sequence[str], None] = "0139"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_gap (
            id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            sync_id     text        NOT NULL,
            symbol      text        NOT NULL DEFAULT '',
            gap_start   date        NOT NULL,
            gap_end     date        NOT NULL,
            state       text        NOT NULL,
            reason      text,
            pull_count  int         NOT NULL DEFAULT 0,
            first_seen  timestamptz NOT NULL DEFAULT now(),
            last_seen   timestamptz NOT NULL DEFAULT now(),
            closed_at   timestamptz,
            CONSTRAINT sync_gap_state_chk CHECK (state IN
                ('open','closed','unreachable','policy_discard','uncertain')),
            CONSTRAINT sync_gap_ident UNIQUE (sync_id, symbol, gap_start)
        )
        """)
    # 存量缺口指标＝state='open' 行数；部分索引让「查 open」与指标查询都走索引
    op.execute("CREATE INDEX IF NOT EXISTS idx_sync_gap_open ON sync_gap (state) WHERE state = 'open'")
    # scope 级扫描（视图式同步的前置 SELECT：sync_id + symbol + 非终态）走此索引
    op.execute("CREATE INDEX IF NOT EXISTS idx_sync_gap_scope ON sync_gap (sync_id, symbol)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sync_gap")
