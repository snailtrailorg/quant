"""批 102b 补：OKX 永续日线的 `start_floor` 回填（**prod 实测后**的证据化下界）。

**为什么是「补」**：迁移 0136 故意把 `sync_config.okx_perp_daily.start_floor` 留 **NULL**
——当时 OKX `history-candles` 的最早可得日**未经实证**（dev 出不到 OKX，直连超时），
按「start_floor 只填已实证下界、其余 NULL，不臆造」的既有纪律留空。

**本迁移填什么、凭什么**（2026-10-06 prod 经 AWS 代理 `54.248.171.145:12345` 只读实测）：
- OKX **裁减了 K 线历史**：`instId=BTC-USDT-SWAP` / `ETH-USDT-SWAP` 的 `bar=1Dutc` 日线
  **最早恰为 2020-01-01**——`after=2020-01-01` → 空；`after=2020-02-15` → `[2020-01-01 ~ 2020-02-14]`；
  `after=2020-03-24` → `[2020-01-01 ~ 2020-03-23]`。两标的**同日**起 ⇒ 是**全局保留窗**，非按标的差异。
- ⚠️ **`instruments.listTime` 不是数据下界**（本迁移不采用它）：BTC/ATOM 的 `listTime=2019-11-12`、
  FIL 甚至是 `2019-08-10`，但 K 线 API 在 2019 **全空** ⇒ 若拿 `listTime` 当 floor，回补会白烧
  **限频额度**（20 req/2s，共享代理出口 IP，会拖累同出口的其它消费方）。故 floor 取**实测的 K 线边界**。

**写入纪律**：`WHERE id='okx_perp_daily' AND start_floor IS NULL`——**不覆盖运维手改**（若运维已按
自身判断填过值，本迁移让位）。幂等（复跑同结果）。
**downgrade**：仅当现值仍等于本迁移所设值时才置 NULL——同样不覆盖手改。

**写法**：`op.execute(<内联字面量 SQL>)`（`--sql` 离线渲染安全，同 0132~0136）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0137"
down_revision: Union[str, Sequence[str], None] = "0136"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 只填 NULL 行（不覆盖运维手改）；幂等
    op.execute(
        "UPDATE sync_config SET start_floor = DATE '2020-01-01' "
        "WHERE id = 'okx_perp_daily' AND start_floor IS NULL")


def downgrade() -> None:
    # 只回收本迁移所设的值（现值被手改成别的则不动）
    op.execute(
        "UPDATE sync_config SET start_floor = NULL "
        "WHERE id = 'okx_perp_daily' AND start_floor = DATE '2020-01-01'")
