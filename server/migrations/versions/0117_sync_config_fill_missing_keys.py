"""批 83b 步 0：sync_config 键集补全（3 键无 cfg 行的代码项补行）。

**背景**：`sync_config` 实查 17 行 vs 应有 20 行——`_VIA_KIND_IDS`（代码可调度的 bar 族
6 键）里有 3 键在 DB 里**没有配置行**：`astock_minute` / `astock_minute_5min` / `index_daily`。
成因（史实）：这 3 行只写在 `scripts/init-seed.sql` 里，而 init-seed 只在**首建库**跑
（`ON CONFLICT DO NOTHING`）；此后经迁移链建起的库（含 staging/生产）自然缺这 3 行。
其余 9 键由迁移 0045 补过，故只有这 3 键漏。

**影响**：DataManage 页看不到这 3 项、不能配 schedule/启用，`sync_config` 作为"配置面单一
真相源"出现空洞——而代码侧明明认它们（`sync()` 能按 sync_id 调度）。

**三键的 enabled 取值（重要）**：
- `astock_minute` / `astock_minute_5min` → **false**。依据迁移 0044 明文裁定（其 docstring
  原话）：这两条共用 handler 遍历全静态列表，「1 次/分钟限速下是**风暴源**」，由池驱动
  `sync_pools_minute` 替代。注意 `init-seed.sql` 字面写 `'true'`（0044 之前的旧值），
  0044 用 UPDATE 关掉了它们——**本迁移补行必须带 0044 的终态（false）**，否则等于把一条
  已被判定为风暴源的同步重新打开。这里以"更晚的裁定 0044"为准，不以 seed 字面为准。
- `index_daily` → **true**（沪深300 基准，回测基准对比用，无风暴风险）。

**可调度性（P2 要求）**：三键均已在 `data_sync.engine._VIA_KIND_IDS`（bar 族静态路由）内，
且在 `sync_kind_config` 有归置行（0094/0106），故补行即**真的可被 `sync()` 调度**，
不是只往表里塞三条死数据。守门见 `tests/test_sync_config_coverage.py`。

**downgrade 语义**：与 0045 同款（seed 类迁移的既有惯例）——DELETE 这 3 行。
⚠️ 若目标库的这 3 行**先于本迁移就存在**（跑过 init-seed 的库），upgrade 的
`ON CONFLICT DO NOTHING` 不会改它、而 downgrade 会把它删掉。本批以"回滚=回到迁移前
配置面"为口径沿用 0045 惯例；生产回滚前请先确认这 3 行不是既有配置。

Revision ID: 0117
Revises: 0116
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0117"
down_revision: str = "0116"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (id, name, tushare_api, pg_table, data_type, sync_mode, schedule, trade_day_filter, enabled, description)
# schedule/trade_day_filter 取自 init-seed.sql 的同名变量（ASTOCK_MINUTE_SCHEDULE='0 16 * * 1-5'
# / ASTOCK_MINUTE_FILTER='trade_day'；INDEX_DAILY_SCHEDULE='30 16 * * 1-5' / INDEX_DAILY_FILTER='trade_day'）
_ROWS = (
    ("astock_minute", "A股分钟线1分", "pro.stk_mins", "bar_1min", "astock", "incremental",
     "0 16 * * 1-5", "trade_day", "false",
     "A股1分钟K线，per-symbol拉取（stk_mins需2000积分，全市场量大）。0044：全市场分钟同步已禁用（风暴源），改池驱动"),
    ("astock_minute_5min", "A股分钟线5分", "pro.stk_mins", "bar_5min", "astock", "incremental",
     "0 16 * * 1-5", "trade_day", "false",
     "A股5分钟K线，per-symbol拉取。0044：全市场分钟同步已禁用（风暴源），改池驱动"),
    ("index_daily", "基准指数日线", "pro.index_daily", "bar_index", "index", "incremental",
     "30 16 * * 1-5", "trade_day", "true",
     "沪深300基准指数日线，回测基准对比"),
)


def upgrade() -> None:
    for (sid, name, api, table, dtype, mode, sched, filt, enabled, desc) in _ROWS:
        # 参数化走 op.get_bind()（alembic 惯例），避免手拼字面量引号
        op.get_bind().exec_driver_sql(
            "INSERT INTO sync_config "
            "(id, name, tushare_api, pg_table, data_type, sync_mode, schedule, "
            " trade_day_filter, enabled, description) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (sid, name, api, table, dtype, mode, sched, filt, enabled == "true", desc))


def downgrade() -> None:
    for row in _ROWS:
        op.get_bind().exec_driver_sql("DELETE FROM sync_config WHERE id=%s", (row[0],))
