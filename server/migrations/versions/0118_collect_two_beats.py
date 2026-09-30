"""批 83b：两条原独立 beat 收编为 sync_config 驱动的同步项。

**收编对象**（任务书 83b 点 3「四条硬编码 beat 收编」中的两条；另两条
`pool-data-sync` / `pool-data-full-calibrate` 随 fetch 契约重写收编）：
- `static-list-sync`（beat 周日 04:37）→ sync_id `static_symbols`
- `convertible-terms-sync`（beat 每日 03:43）→ sync_id `convertible_terms`

**为什么这样收编**：`tier1` 九键自 0045 起就是"配置驱动"（sync_config 行 + `_HANDLERS`
注册 + `data_sync_scheduler` 扫描 cron）——而这四条 beat 是绕开该体系的硬编码路径
（`app.py` 直接挂 `@app.task` 的 crontab）。硬编码路径的代价：`sync_log` 留痕口径不同、
不吃 `sync()` 的防重 SyncLock、失败不进 sync_config.last_status/告警链、也没法在
DataManage 页配置/停用。收编后四条与 tier1 同等待遇。

**调度口令等价性**（行为等价的关键）：本迁移的 cron 串**逐字对齐**原 beat 的 crontab：
| beat 原写法                                   | sync_config.schedule | trade_day_filter |
|-----------------------------------------------|----------------------|------------------|
| `crontab(day_of_week=0, hour=4, minute=37)`   | `37 4 * * 0`         | none             |
| `crontab(hour=3, minute=43)`                  | `43 3 * * *`         | none             |
`trade_day_filter='none'`：原 beat 无交易日过滤（周日/每日都跑），若填 trade_day 会在
非交易日静默少跑——那是行为变化，本批要求等价，故 none。

**downgrade**：删这两行（同 0045 的 seed 类惯例）。⚠️ rollback 后**必须同时回滚代码**
（把 app.py 的 beat 条目与 tasks.py 的 @app.task 恢复）——否则这两条同步从此无人调度
（配置面行被删 + 代码面 beat 已退役 = 能力真空）。整版本 rollback 是本项目既定口径。

Revision ID: 0118
Revises: 0117
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0118"
down_revision: str = "0117"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (id, name, tushare_api, pg_table, data_type, sync_mode, schedule, trade_day_filter, enabled, description)
_ROWS = (
    ("static_symbols", "静态标的清单", "pro.stock_basic", "static_symbols", "astock", "full",
     "37 4 * * 0", "none", "true",
     "原 static-list-sync beat 收编（批 83b）。在市 A 股清单 → static_symbols，周日 04:37"),
    ("convertible_terms", "可转债条款", "pro.cb_basic", "convertible_terms", "convertible", "full",
     "43 3 * * *", "none", "true",
     "原 convertible-terms-sync beat 收编（批 83b）。活跃转债条款逐只拉取（单轮限 50 只），每日 03:43"),
)


def upgrade() -> None:
    for (sid, name, api, table, dtype, mode, sched, filt, enabled, desc) in _ROWS:
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
