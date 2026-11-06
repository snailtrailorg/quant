# EXPAND-CONTRACT: phase=expand pair=None
"""批 171d-A：单轮最大跨度——`sync_config.max_span_days` 列 + `sync_max_span_days` 全局键。

任务＝`flow/任务/批171d-A-单轮最大跨度与截断反馈.md`（v2.1 定稿：双盲审 5P0+快核全收）。
D31 §5 裁定 5：冷启动分 N 轮平稳跑完——`终点 = min(请求终点, 起点+最大跨度)`。

- **列**：`sync_config.max_span_days INT NULL`（尾列；NULL=回落全局键；任务级覆盖）。
  任务级列不入配置端点（对齐 retention 迁移层惯例——2026-11-06 裁定：全局键=用户旋钮
  无门禁，任务级=策略列）。**本批不加 sync_floor_default**（批 B 域，防两批纠缠）。
- **键行**：`system_config.sync_max_span_days`=366（四列 INSERT 仓例 27 处先例）。
  SYSTEM_CONFIG_BOUNDS 下界 1 上界不钳（0/负数=语义错误；hi=None 机制支持）。

**downgrade**：删列删键行（对称）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0170"
down_revision: Union[str, Sequence[str], None] = "0169"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE sync_config ADD COLUMN IF NOT EXISTS max_span_days INTEGER")
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description) VALUES ("
        "'sync_max_span_days', '366', 'int', "
        "'单轮同步最大跨度（天，含两端）——冷启动/回补分轮防一次全史马拉松"
        "（批 171d-A/D31 §5）。任务级 sync_config.max_span_days 覆盖') "
        "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    op.execute("ALTER TABLE sync_config DROP COLUMN IF EXISTS max_span_days")
    op.execute("DELETE FROM system_config WHERE key = 'sync_max_span_days'")
