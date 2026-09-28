"""批 78：系统监控 CPU 卡+曲线 FIR 滤波（方案 `flow/任务/批78-监控页CPU卡与曲线滤波.md` v2）。

- system_metric 加 5 列（DOUBLE PRECISION，NULL 允许——历史行无值，series 查询端过滤）：
  cpu_used（0-1 占用率，无字节语义）/ mem_used_avg / swap_used_avg / disk_used_avg / cpu_used_avg
  （批78 FIR：5 分钟矩形窗均值落库值——前端曲线画 avg、大数字/告警用最新真值）
- system_config seed 3 键（0076 惯例）：alert_cpu_warn=0.7（用户知情裁定——0.7 档盘中瞬时
  越限告警属预期）/ alert_cpu_crit=0.9 / collect_period_cpu=30
- 纯 ADD COLUMN + 数据 seed=非破坏性，不走 allow_contract
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0112"
down_revision: str = "0111"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEEDS = [
    ("alert_cpu_warn", "0.7", "float", "CPU 告警阈值-警告档（占用率）"),
    ("alert_cpu_crit", "0.9", "float", "CPU 告警阈值-严重档（占用率）"),
    ("collect_period_cpu", "30", "int", "CPU 采集周期（秒）"),
]


def upgrade() -> None:
    op.add_column("system_metric", sa.Column("cpu_used", sa.Double()))
    op.add_column("system_metric", sa.Column("mem_used_avg", sa.Double()))
    op.add_column("system_metric", sa.Column("swap_used_avg", sa.Double()))
    op.add_column("system_metric", sa.Column("disk_used_avg", sa.Double()))
    op.add_column("system_metric", sa.Column("cpu_used_avg", sa.Double()))
    for key, value, vtype, desc in _SEEDS:
        op.execute(
            "INSERT INTO system_config (key, value, value_type, description) "
            f"VALUES ('{key}', '{value}', '{vtype}', '{desc}') "
            "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    for key, _, _, _ in _SEEDS:
        op.execute(f"DELETE FROM system_config WHERE key = '{key}'")
    op.drop_column("system_metric", "cpu_used_avg")
    op.drop_column("system_metric", "disk_used_avg")
    op.drop_column("system_metric", "swap_used_avg")
    op.drop_column("system_metric", "mem_used_avg")
    op.drop_column("system_metric", "cpu_used")
