"""批 103b：聚宽（JQData）A 股日线历史切片落 `bar_1d`。

**定位（威廉姆 2026-10-06 裁定）**：聚宽试用账号窗口＝**前 15 个月 ~ 前 3 个月**（实测绝对区间
`2025-06-28 ~ 2026-07-05`，非滚动），**拿不到最近 3 个月** ⇒ **不能**当 `astock_daily` 的常规
替代源（切过去最近 3 月无人回补、数据倒退且误切难察）。故本项用**独立 sync_id**
`astock_daily_jq`：落同一张 `bar_1d`、同键 `(symbol, ts)` upsert，但**不占** `astock_daily`
的切换位（两行互不相扰；`astock_daily` 的 provider 下拉仍只有 tushare）。

**为什么 A 股各源可以同表同键**（威廉姆：「A 股不像加密各所 bar 都不一样，各源是复制同一份数据」）：
实测同键对照（000001，2026-06-01）两源 `volume/amount` **完全一致**（95,459,569 / 1,042,306,456）。
**唯一不可互换的是复权因子**：聚宽 `factor` 归一化基准与 tushare 的 `adj_factor` 不同（146.360309
vs 134.5794），故 adapter 的 `to_bar_rows` **恒置 `adj_factor=None`**，由落库侧既有的
`COALESCE(EXCLUDED.adj_factor, bar_1d.adj_factor)`（F-F2）保住 tushare 已回填值。
证据与设计见 `flow/任务/批103b-聚宽真接.md`。

**本迁移只插两行配置**（expand-only：无 DDL、无 DELETE/RENAME）：
1. `sync_config.astock_daily_jq`——`provider='joinquant'`、`supports_backfill=true`（handler 真吃
   `backfill_from` 续传）、`start_floor` **留 NULL**（窗口动态取自 `get_account_info()`，
   写死值会成为谎言——续期/到期会变）、`trade_day_filter='trade_day'`（A 股有交易日历）。
2. `sync_kind_config.astock_daily_jq`——归置 `(kind=bar_daily, sub_kind=stock)` → `bar_1d`。
   与 `astock_daily` 同族：品类轴语义正确（**同一份数据的不同来源**，非不同品类）；
   `behavior_equiv` 的品类过滤按 symbol 集合，故两源行归入同品类属正确行为。

**enabled=true 的理由**：本项**不是**死构件——凭证明文缺失时 `JoinQuantDataSource.get_client()`
抛 `ProviderConfigError`，同步**响亮标红**并给出可执行文案（「请在数据源页为 joinquant 填写
account/password」）。这正是「配凭证前可见停红、而非静默无数据」的设计意图。

**downgrade**：删这两行（对称）。`bar_1d` 里已落的数据不随配置回滚删除（数据与配置解耦）。

**写法**：`op.execute(<内联字面量 SQL>)`——离线渲染（`alembic upgrade --sql`）下
`exec_driver_sql` 会炸（同 0132 的教训）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0133"
down_revision: Union[str, Sequence[str], None] = "0132"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SYNC_ID = "astock_daily_jq"


def upgrade() -> None:
    # 1) sync_config 行（幂等：ON CONFLICT DO NOTHING——尊重运行期改动，复跑不覆盖运维编辑）
    op.execute(
        "INSERT INTO sync_config (id, name, tushare_api, pg_table, data_type, sync_mode, "
        "schedule, enabled, trade_day_filter, provider, supports_backfill, start_floor, "
        "description) VALUES ("
        "'astock_daily_jq', '聚宽A股日线(历史切片)', 'joinquant.get_price', 'bar_1D', 'astock', "
        "'incremental', '10 17 * * 1-5', true, 'trade_day', 'joinquant', true, NULL, "
        "'聚宽 JQData A股日线。试用窗口=前15月~前3月（动态取自 get_account_info，故 start_floor 留空）；"
        "每日100万行额度；连接数=1（provider 级互斥）。独立 sync_id，不占 astock_daily 切换位。') "
        "ON CONFLICT (id) DO NOTHING")
    # 2) sync_kind_config 归置行（与 astock_daily 同族 bar_daily/stock）
    op.execute(
        "INSERT INTO sync_kind_config (sync_id, kind, sub_kind, pg_table, pk_cols, "
        "float_cols, text_cols, rebuild) VALUES ("
        "'astock_daily_jq', 'bar_daily', 'stock', 'bar_1d', "
        "'{symbol,ts}', '{open,high,low,close,volume,amount}', '{}', 'incremental') "
        "ON CONFLICT (sync_id) DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM sync_kind_config WHERE sync_id = '{_SYNC_ID}'")
    op.execute(f"DELETE FROM sync_config WHERE id = '{_SYNC_ID}'")
