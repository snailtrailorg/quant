"""批 99：sync_config 增「可回补能力」两列（supports_backfill / start_floor）。

**问题**：DataManage 的回补表单门写死 `sync_mode === 'incremental'`，而 `sync_mode` 是
**形态标签**、不参与任何分派（`sync()` 按 `_HANDLERS`/`_VIA_KIND_IDS` 路由）——用它当
「能不能回补」的能力门，两个方向都错：

- **该有的没有**：`trade_cal` 后端 `_sync_trade_cal` 真吃 `backfill_from`（批 97 实现逐年拉），
  但它 `sync_mode='full'` ⇒ 界面没有入口，prod 回补链第一环卡死；
- **不该有的有了**：`pool_data` 是 `incremental` ⇒ 界面渲染回补起点，但 handler 走
  `run_pool_sync` **完全不读该参数** ⇒ 点了只跑一轮常规增量、还报成功（沉默空转）。

**改法**：把能力显式化到**项层**（不是族层）——理由是实测反例：同一 `kind=featured_daily`
下 `margin_detail_sync` 吃起点而 `cyq_chips`（池内）不吃；`financial_stmt` 下 `forecast_sync`
吃而池内三张不吃。「吃不吃起点」由**实现**决定，不由数据族决定，故不能落在
`sync_kind_config`。

- `supports_backfill boolean NOT NULL DEFAULT false`——该 sync_id 的 handler 是否消费
  `backfill_from`。真源仍是代码结构（`_VIA_KIND_IDS` ∪ `_TIER1_BATCH` ∪ {astock_basic,
  trade_cal}），本列是它的**落库投影**，由 `tests/test_batch99_*` 双向钉住。
- `start_floor date NULL`——数据起点下界（早于此纯空跑，对齐「不机械空跑」立法）。
  NULL = 未声明（不设下限），用于前端 date-picker 下限；**填已知值，不臆造**。

**seed 值（本迁移按现状落 15/9 与 2 个已知下界）**：
- `supports_backfill=true` 15 项 = bar 族 6 + tier1 批量 7 + 交易日历 + A股基本面；
  其余 9 项（静态清单 5 / 全量重建 2 / 池内 2）保持 false。
- `start_floor`：`astock_basic='1990-12-19'`（上游 daily_basic 实测最早行）、
  `index_daily='2005-04-08'`（`engine._sync_via_kind` 已硬编码的基准指数全量起点，
  本列是它的显式化，**本批不改 handler 读取**，行为零漂移）。

**expand-only**：只加列 + 回填，无 DROP/RENAME ⇒ 阶段 4 破坏性 DDL 门不拦，回滚只回代码。

**downgrade**：删两列（对称；列内无历史依赖）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0131"
down_revision: Union[str, Sequence[str], None] = "0130"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 真源＝代码结构（engine.py）：_VIA_KIND_IDS ∪ _TIER1_BATCH ∪ {astock_basic, trade_cal}
_BACKFILLABLE = (
    # bar 族 6（_VIA_KIND_IDS，_sync_via_kind 用 backfill_from 作起点）
    "astock_daily", "etf_daily", "cb_daily", "index_daily",
    "astock_minute", "astock_minute_5min",
    # tier1 批量 7（_TIER1_BATCH，逐交易日循环起点）
    "stk_limit_sync", "moneyflow_sync", "margin_detail_sync", "top_list_sync",
    "block_trade_sync", "cyq_perf_sync", "forecast_sync",
    # 单表专项 2（真读 backfill_from）
    "astock_basic", "trade_cal",
)

_START_FLOOR = {
    "astock_basic": "1990-12-19",
    "index_daily": "2005-04-08",
}


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE sync_config ADD COLUMN supports_backfill boolean NOT NULL DEFAULT false")
    op.get_bind().exec_driver_sql(
        "ALTER TABLE sync_config ADD COLUMN start_floor date")
    # 回填真源（幂等：无条件置 false 再点 true，复跑结果一致）
    op.get_bind().exec_driver_sql("UPDATE sync_config SET supports_backfill = false")
    ph = ", ".join(["%s"] * len(_BACKFILLABLE))
    op.get_bind().exec_driver_sql(
        f"UPDATE sync_config SET supports_backfill = true WHERE id IN ({ph})",
        tuple(_BACKFILLABLE))
    for sid, floor in _START_FLOOR.items():
        op.get_bind().exec_driver_sql(
            "UPDATE sync_config SET start_floor = %s WHERE id = %s", (floor, sid))


def downgrade() -> None:
    op.get_bind().exec_driver_sql("ALTER TABLE sync_config DROP COLUMN start_floor")
    op.get_bind().exec_driver_sql("ALTER TABLE sync_config DROP COLUMN supports_backfill")
