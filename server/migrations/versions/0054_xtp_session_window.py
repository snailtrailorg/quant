"""每日连接窗配置键 seed（P2 批 2026-08-28，docs/任务/P2-场内基金与每日连接窗批.md）。

`xtp_session_lead_min`/`xtp_session_lag_min` 进 system_config（int，默认 10/10）——
Web SystemConfig 通用卡片自动可编辑（前端零改动）。任一 <=0=禁用日窗（永久连接，
旧行为逃生门）；改动重启生效（main 层启动读一次传纯参）。
"""
from __future__ import annotations
from alembic import op

revision: str = "0054"
down_revision: str = "0053"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description, updated_by) VALUES "
        "('xtp_session_lead_min', '10', 'int', "
        "'XTP 每日连接窗：开盘前提前建连分钟数（锚 9:15 集合竞价，默认 10→9:05 建连）；"
        "0 或与 lag 同设 0=禁用日窗（永久连接）', 'migration') "
        "ON CONFLICT (key) DO NOTHING")
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description, updated_by) VALUES "
        "('xtp_session_lag_min', '10', 'int', "
        "'XTP 每日连接窗：收盘后延迟断开分钟数（锚 15:00 收盘，默认 10→15:10 logout）；"
        "0=禁用日窗。A 股专用（加密市场不读）', 'migration') "
        "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    op.execute("DELETE FROM system_config WHERE key IN "
               "('xtp_session_lead_min', 'xtp_session_lag_min')")
