# EXPAND-CONTRACT: phase=expand  # 展示文案与标签值归一（等价替换——data_type 无逻辑键消费，仅页面直渲）
"""批 127 补漏：sync_config 展示面正名（用户发现 2026-10-18：「数据管理仍见 ETF日线行情」）。

C 档盘点漏项：sync_config.name 与 data_type 是数据管理页**直渲的用户可见面**
（name=任务名、data_type=品类 tag），被误归入「数据集内部标识」未改。
已核实 data_type 无逻辑键消费（仅 SELECT 直出前端）——UPDATE 等价替换走 expand。
sync id（etf_list/etf_daily）保留（内部标识，Step3 裁定）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0154"
down_revision: Union[str, Sequence[str], None] = "0153"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE sync_config SET name='场内基金列表' WHERE id='etf_list'")
    op.execute("UPDATE sync_config SET name='场内基金日线行情' WHERE id='etf_daily'")
    op.execute("UPDATE sync_config SET data_type='listed_fund' WHERE data_type='etf'")


def downgrade() -> None:
    op.execute("UPDATE sync_config SET data_type='etf' WHERE data_type='listed_fund'")
    op.execute("UPDATE sync_config SET name='ETF日线行情' WHERE id='etf_daily'")
    op.execute("UPDATE sync_config SET name='ETF基金列表' WHERE id='etf_list'")
