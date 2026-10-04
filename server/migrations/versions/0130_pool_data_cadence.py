"""批 92：池数据同步频率 5min→每日 02:00（季度级/公告级数据不配 5 分钟轮）。

**问题**：0119 收编时逐字保留原 beat 的 `*/5 * * * *`（5 分钟间隔），但池内 10 表是
财务四表（季报，按公告日增量）+ 筹码/股东/事件表（季频/事件驱动）——季度级数据每 5 分钟
全量重写一轮（实测日志 100 轮全「成功 / 4470 条重写」），纯属上游 API 配额与库侧 upsert
写放大的双重浪费（4470 行/轮）。

**改法**：`pool_data` 增量轮改每日 02:00（`0 2 * * *`）——公告多在盘后/晚间发布，
次日凌晨跑可得「昨日及以前」全部公告；`trade_day_filter` 维持 `none`（迟到公告周末照发）。
`pool_data_full_calibrate`（周日 04:07 全量校准）不动——错峰且承担兜底。

**配套**（代码侧，见同批 `data_sync/pool_data.py`）：窗口上界随之下沉为「昨日自然日」
（`run_pool_sync` 的 `upper_str`）+ 回看重叠 `_POOL_OVERLAP_DAYS`——否则 02:00 跑用 today
作上界会恒拉不到当天公告、游标又已越过 ⇒ 静默漏。本迁移只改调度列。

**downgrade**：恢复 `*/5 * * * *` 与收编时描述。

Revision ID: 0130
Revises: 0129
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0130"
down_revision: Union[str, Sequence[str], None] = "0129"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_DESC = ("池内 10 表 per-symbol 拉取（财务4/筹码/股东5），表级游标增量 + 时间盒 280s；"
             "批 92 起每日 02:00 一轮（原 5 分钟轮＝季度级数据重写浪费）")
_OLD_DESC = ("原 pool-data-sync beat 收编（批 83b）。池内 10 表（财务4/筹码/股东5）per-symbol "
             "拉取，表级游标增量 + 时间盒 280s，每 5 分钟一轮")


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "UPDATE sync_config SET schedule=%s, description=%s WHERE id=%s",
        ("0 2 * * *", _NEW_DESC, "pool_data"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "UPDATE sync_config SET schedule=%s, description=%s WHERE id=%s",
        ("*/5 * * * *", _OLD_DESC, "pool_data"))
