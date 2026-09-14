"""批22 迭代：资源阈值/采集周期配置化（system_config seed）。

- 阈值 5 键（float）：alert_mem_warn=0.6（用户裁定缺省 60%）/ alert_mem_crit=0.9 /
  alert_disk_warn=0.8 / alert_disk_crit=0.9 / alert_swap_warn=0.8
- 采集周期 3 键（int 秒）：collect_period_mem=60 / collect_period_disk=3600 / collect_period_swap=300
- 设置页 RunConfig 动态渲染 system_config——seed 后自动出现编辑项，前端零改动

Revision ID: 0076
Revises: 0075
Create Date: 2026-09-15
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0076"
down_revision: str = "0075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEEDS = [
    ("alert_mem_warn", "0.6", "float", "内存告警阈值-警告档（used 占比）"),
    ("alert_mem_crit", "0.9", "float", "内存告警阈值-严重档（used 占比）"),
    ("alert_disk_warn", "0.8", "float", "磁盘告警阈值-警告档（逐挂载点 used 占比）"),
    ("alert_disk_crit", "0.9", "float", "磁盘告警阈值-严重档（逐挂载点 used 占比）"),
    ("alert_swap_warn", "0.8", "float", "交换分区告警阈值-警告档（used 占比）"),
    ("collect_period_mem", "60", "int", "内存采集周期（秒）"),
    ("collect_period_disk", "3600", "int", "磁盘采集周期（秒）"),
    ("collect_period_swap", "300", "int", "交换分区采集周期（秒）"),
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
