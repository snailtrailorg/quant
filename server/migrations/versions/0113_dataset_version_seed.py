"""批 71：H8 版本真源 seed（system_config dataset_version:{kind} 三键——0054 形态先例）。

- 消「Store.cur_version 缺省 1 vs 表列 DEFAULT」漂移（store.py docstring 挂账收口）：
  dataset_version:bar_daily=1（bar_1D）/ dataset_version:bar_minute=2（canonical=bar_1min，
  0095 竞价并入批推 2；立法：bar_minute 一键盖 7 张分钟表，版本=bar_1min 口径——
  非 canonical 分钟表 15/30/60min/1h/4h 列 DEFAULT 1 显式接受）/ dataset_version:index_daily=1（bar_index）
- 读者=Store.cur_version（未来 H12/frozen_or_current 消费方）；_merge_run_lineage 维持
  max(bar 表) 语义不变（0109 立法——实际数据版本比键更真，v2 #7 仲裁）
- ⚠ 勿手改，随数据变更批推进（RunConfig 编辑卡可见）——键/列对账由批 71 验收 SQL 守护
- daily_basic 不 seed：cur_version("fundamental_daily") 现无读方且不在血缘链（v2 #8 正名）
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0113"
down_revision: str = "0112"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEEDS = [
    ("dataset_version:bar_daily", "1", "int", "bar_1D 数据集版本（勿手改，随数据变更批推进）"),
    ("dataset_version:bar_minute", "2", "int", "分钟线数据集版本（canonical=bar_1min 口径；勿手改，随数据变更批推进）"),
    ("dataset_version:index_daily", "1", "int", "bar_index 数据集版本（勿手改，随数据变更批推进）"),
]


def upgrade() -> None:
    for key, value, vtype, desc in _SEEDS:
        op.execute(
            "INSERT INTO system_config (key, value, value_type, description) "
            f"VALUES ('{key}', '{value}', '{vtype}', '{desc}') "
            "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    for key, _, _, _ in _SEEDS:
        op.execute(f"DELETE FROM system_config WHERE key = '{key}'")
