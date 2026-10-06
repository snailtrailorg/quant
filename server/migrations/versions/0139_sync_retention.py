"""批 108·步 3：`sync_config` 增 `retention`（保留策略）——「一列三义」的 `start_floor` 正名（裁定 H）。

**问题**：`start_floor`（迁移 0131）**一列三义**（DB 实测 30 行中 7 行非空）：

| id | 旧 `start_floor` | 真义 |
|---|---|---|
| `binance_perp_daily` | 2019-09-08 | **inception**（上币日；0132 自注「USDT-M 永续上线日」） |
| `okx_perp_daily` | 2020-01-01 | **源可达下界**（0137 明写「实测 K 线边界」） |
| `binance_perp_hourly` | 2026-09-29 | **retention**（存储闸门） |
| `binance_perp_{15min,1min}` | 2026-10-05 | **retention**（存储闸门） |
| `index_daily` | 2005-04-08 | **无 inception 族**的全量起点 |
| `astock_basic` | 1990-12-19 | **无 inception 族**的全量起点 |

同列装载三种语义 ⇒ 任何「按 provider 一刀切」的消费都必错（裁定 H：**拆**，禁一刀切）。

**改法（三处各归其位）**：
- `retention`（**本迁移新增，`engine._get_config` 真读**）＝ 我们**关心**的最早时点（**策略**，每任务）；
- 源可达边界 → `available_range()`（批 108 步 1 硬契约，**接入层**）；
- inception → `security_master.list_date`（批 108 步 2，**生命周期主档**）。

**逐行归义（禁按 provider 一刀切）**：

- `binance_perp_daily` / `okx_perp_daily` → **retention 留 NULL**。旧值分别是 **inception** 与
  **源下限**——**照抄就是把「源属性 / 上币日」坐进「策略列」**，与本次要消灭的一列三义**同病**。
  二者的真值已分别由 SM（步 2）与 `available_range`（步 1）承载。
  ⚠️ 连带（**须运维决策**）：留 NULL ⇒ crypto 族首跑下界＝源界（`binance` `2019-12-31`）＝
  **首跑即全史**（F-1 验收路径）。若不要全史，须显式填 `retention`——那是**策略**取值，本迁移
  **不臆造**（先例＝迁移 `0136` 故意留 NULL、`0137`「只填已实证下界」）。
- `binance_perp_{hourly,15min,1min}` → 照抄（旧值本就是 retention）。
- `index_daily` → 照抄。设计 §九.7：该族**无列表来源 ⇒ 无 inception**，「只能靠 `retention`
  （＋源下界）」；同时它替换 `engine._sync_via_kind` 里硬编码的 `20050408`（值不变、行为零漂移）。
- `astock_basic` → 照抄。同为「无 inception 族」的全量起点；`retention` 是它的引擎消费点
  （`_sync_astock_basic` 下界夹取），不填则那行的下界只剩源界。

**expand-only（破坏性两步走的**第一步**）**：本版**只加列 + 回填**，`start_floor` **保留不删**
（本版起**零读点**：`web_api/routes/sync.py` 与前端已切 `retention`）。下版再 `DROP COLUMN`
——回滚只回代码、schema 不后退（`flow/任务/批85-破坏性迁移两步走立法.md`）。

**downgrade**：删 `retention`（对称；本迁移不触碰 `start_floor`，故降级后 0131 的列仍在）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0139"
down_revision: Union[str, Sequence[str], None] = "0138"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 逐行归义后的 retention 真值（**未列出者保持 NULL＝未声明**，不臆造）。真源＝本文件注释的上表，
# 与 `tests/test_batch108_window_max.py` 的双向钉守（DB 值 ↔ 本字面量）同源。
_RETENTION: dict[str, str] = {
    "binance_perp_hourly": "2026-09-29",
    "binance_perp_15min": "2026-10-05",
    "binance_perp_1min": "2026-10-05",
    "index_daily": "2005-04-08",
    "astock_basic": "1990-12-19",
}


def upgrade() -> None:
    # DML 一律 `op.execute(<内联字面量>)`——`op.get_bind().exec_driver_sql(<带参>)` 在
    # `alembic upgrade --sql` 离线渲染下炸 `MockConnection has no attribute exec_driver_sql`
    # （批 101 由往返用例抓到；0129/0131 有同族既存债，已应用迁移不动）。
    op.execute("ALTER TABLE sync_config ADD COLUMN IF NOT EXISTS retention date")
    for sid, d in _RETENTION.items():
        # `AND retention IS NULL` 守卫 + `IF NOT EXISTS` ⇒ **幂等**（部署中断强制复跑安）且
        # **不覆盖运维手改**（人力填过值就让位——「运维编辑优先于迁移 seed」立法）。
        op.execute(f"UPDATE sync_config SET retention = DATE '{d}' "
                   f"WHERE id = '{sid}' AND retention IS NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE sync_config DROP COLUMN IF EXISTS retention")
