"""批 110：`sync_gap` 判据**状态载体**两列——「连续 N 轮同缺口」从散文变**可判定**（expand-only）。

任务＝`flow/任务/批110-对账判据可判定化.md` v3；上位设计 `flow/方案/同步窗口与参数分层-设计.md`
§八.5（删除门控＝可观测判据）。

**为什么加这两列**：母设计 §八.5 的门控判据「连续 N 轮 per-symbol 对账命中同一缺口且零误报」
在批 109 上产后**本体不存在**（`sync_gap` 无「连续命中轮次」列；「零误报」全仓无定义）——
判据不可判定＝门控不存在。这两列是判据的**状态载体**：

- `hit_rounds`：**连续**命中轮次（**含断档复位**——见 `engine._sync_gap_sync` 的维护语义）；
- `last_hit_round`：最后一次命中的**轮次 id**（Valkey `INCR reconcile:round`，严格单调）——
  复位判据 `last_hit_round == round_id-1` 由它承载。

**为什么**没有** `false_rounds`（步 4 代码双盲审 P1-2 裁定删列）**：v3 原设计三列，第三列
`false_rounds`（「误报证据累计」）经双盲审＋主会话一手 `grep` 确认**全仓无写入点**＝死列。
根因：R2 宣称的「可自动判定的两类误报」在本结构的**单分支**语义下**结构不可达**——同一
`(symbol, anchor)` 一轮内只走 opened／closed／uncertain **之一** ⇒ 任何写入都恒为 `0`。
保留一个「声明不被执行面消费」的列违反本仓红线 ⇒ **删列**；R2 的真实定位（**防御性哨兵**，
非自动门）写入 `_gap_judge` docstring 与方案 §R2。**当轮**误报证据仍由 `_gap_judge` 计算并进
摘要（不落表）。

**为什么是「无条件推进的序号」而非墙钟**（批 110·V1）：beat 周频下**手动重跑可达同秒**（碰撞）、
NTP 可回拨（非单调）、DB `MAX()+1` 派生在「该轮**所有** scope 均未写行」时**不推进**（断档被当连续）。

**写法（仓例）**：`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`（**纯 expand**，无破坏性 DDL
⇒ 不需两步走）；全部 `op.execute(<内联字面量>)`（`--sql` 离线渲染安全，同 0132~0140）。
`NOT NULL DEFAULT 0` 在 PG 11+ 是**元数据操作**（不重写表）。

**新增列追加在末尾**（非插中段）：与 `0140` 的列序一致地**追加** ⇒ `schema_expectations.txt`
的列清单同步追加，列形状门可判别。

**downgrade**：`DROP COLUMN IF EXISTS`（对称回滚；两列是**派生**判据状态，`sync_gap` 整体可
重建 ⇒ 无信息损失）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0141"
down_revision: Union[str, Sequence[str], None] = "0140"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE sync_gap ADD COLUMN IF NOT EXISTS hit_rounds int NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE sync_gap ADD COLUMN IF NOT EXISTS last_hit_round bigint NOT NULL DEFAULT 0")


def downgrade() -> None:
    # 逆序回收（对称）
    op.execute("ALTER TABLE sync_gap DROP COLUMN IF EXISTS last_hit_round")
    op.execute("ALTER TABLE sync_gap DROP COLUMN IF EXISTS hit_rounds")
