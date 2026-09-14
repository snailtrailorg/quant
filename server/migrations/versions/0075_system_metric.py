"""批22 系统监控：system_metric 指标时间序列表 + disk_monitor_paths 设置项。

- system_metric 宽表（每 5min 一行，30 天保留）：mem/swap/disk 的 total/used
- system_config seed：disk_monitor_paths（磁盘监控路径，':' 分隔多挂载点，缺省 '/'）

Revision ID: 0075
Revises: 0074
Create Date: 2026-09-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0075"
down_revision: str = "0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE system_metric ("
        " ts timestamptz PRIMARY KEY,"
        " mem_total bigint, mem_used bigint,"
        " swap_total bigint, swap_used bigint,"
        " disk_total bigint, disk_used bigint)")
    # 磁盘监控路径设置项（缺省 /，可改 ':' 分隔多挂载点；配置驱动非硬编码）
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description) "
        "VALUES ('disk_monitor_paths', '/', 'text', '磁盘监控挂载点，冒号分隔多路径') "
        "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS system_metric")
    op.execute("DELETE FROM system_config WHERE key = 'disk_monitor_paths'")
