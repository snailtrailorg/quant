"""批 83b：池内深度数据两条 beat 收编为 sync_config 驱动的同步项（4 beat 收编收尾）。

**收编对象**（任务书 83b 点 3；另两条 `static-list-sync` / `convertible-terms-sync`
已在 0118 收编）：
- `pool-data-sync`（beat `schedule=300.0` 间隔）→ sync_id `pool_data`
- `pool-data-full-calibrate`（beat `crontab(day_of_week=0, hour=4, minute=7)`）
  → sync_id `pool_data_full_calibrate`

**为什么这两条要连 fetch 契约一起重写**：池数据是**独立子系统**（10 表 per-symbol 拉取 +
表级游标 + 时间盒 + SyncLock），原实现把「源特定拉取」与「通用编排」揉在 `data_sync/
pool_data.py` 一层（源 API 名/源参数/列名/值归一全在 data_sync 层硬编码）——直接收编进
`_HANDLERS` 只会把硬编码搬个位置。按任务书「分层判据」：源特定部分下沉
`POOL_TABLE_SPECS` + `TushareAdapter.fetch`（fetch 契约），通用编排（游标/时间盒/锁/落库）
留引擎层。收编后池数据与 tier1 同等待遇（sync_log 留痕/防重锁/失败告警/页面可配）。

**调度口令等价性**（行为等价的关键）：
| beat 原写法                                        | sync_config.schedule | trade_day_filter |
|----------------------------------------------------|----------------------|------------------|
| `schedule: 300.0`（celery interval 秒）             | `*/5 * * * *`        | none             |
| `crontab(day_of_week=0, hour=4, minute=7)`         | `7 4 * * 0`          | none             |
`trade_day_filter='none'`：原 beat 两个都无交易日过滤（池数据是季频/公告驱动，非交易日也应
拉——迟到公告在周末照发），填 trade_day 会静默少跑，属行为变化。

**执行机制**：这两行由 `data_sync_scheduler`（300s 扫描 cron）触发时**异步派发**
（`tasks._SYNC_ASYNC_DISPATCH`）——池轮时间盒 280s 逼近该 beat 的 300s 软超时，
内联跑会挤掉同周期其他到期同步项。注册制不受影响（行在 sync_config、handler 在
engine._HANDLERS），异步只是执行机制。

**downgrade**：删这两行（同 0118 惯例）。⚠️ rollback 后**必须同时回滚代码**（恢复 app.py
的两条 beat 条目）——否则池数据同步从此无人调度（配置面行被删 + 代码面 beat 已退役）。
整版本 rollback 是本项目既定口径。本迁移**不动表结构**（只增行数据），schema_expectations
无需重生成。

Revision ID: 0119
Revises: 0118
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0119"
down_revision: str = "0118"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (id, name, tushare_api, pg_table, data_type, sync_mode, schedule, trade_day_filter, enabled, description)
# tushare_api/pg_table 为**虚拟聚合句柄**（池数据经 adapter.fetch 拉 10 个源接口写 10 张表，
# 无单一源 API/目标表）——两列在本行仅作页面展示（DataManage 默认隐藏这两列，opt-in 可见）；
# 引擎侧不读（handler 由 sync_id 路由，表名/kind 一律来自 sync_kind_config 归置行）。
_ROWS = (
    ("pool_data", "池内深度数据同步", "pro.pool_data", "pool_data(10表)", "pool", "incremental",
     "*/5 * * * *", "none", "true",
     "原 pool-data-sync beat 收编（批 83b）。池内 10 表（财务4/筹码/股东5）per-symbol 拉取，"
     "表级游标增量 + 时间盒 280s，每 5 分钟一轮"),
    ("pool_data_full_calibrate", "池内深度数据全量校准", "pro.pool_data", "pool_data(10表)", "pool",
     "full", "7 4 * * 0", "none", "true",
     "原 pool-data-full-calibrate beat 收编（批 83b）。周日全量校准（无视游标窗口，游标照常推进）"
     "——迟到公告/上游改历史/长期失败冻结窗口的兜底，04:07 错峰"),
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
