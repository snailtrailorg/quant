# EXPAND-CONTRACT: phase=expand pair=None
"""批 171d-B：地板全局化——`sync_floor_default` 全局键 + trade_cal 声明性 seed。

任务＝`flow/任务/批171d-B-地板全局化.md`（v2 快核过，82f5eca）。D31 §2 v2.4：
有效地板 = max(sync_floor_default, retention, inception, source_earliest)——max 语义
对深史任务同样生效（astock_basic 1990/index_daily 2005 ⇒ 2014；深史回补=临时调低全局键）。

- **键行**：`sync_floor_default`='2014-01-01'（value_type='date'——update 端点 date 分支
  校验格式，读侧 fail-open）。
- **trade_cal seed**：retention='1990-12-19'——**声明性文档值**（handler 不经地板链，
  钉「日历自持全史」意图防未来收编误配；条件 UPDATE 防误清运维手改值）。

**downgrade**：删键行 + trade_cal 条件复位（仅当值仍为本迁移所 seed 时）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0171"
down_revision: Union[str, Sequence[str], None] = "0170"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description) VALUES ("
        "'sync_floor_default', '2014-01-01', 'date', "
        "'全局地板（有效地板四源取 max 之第四源：max(本键, 任务 retention, inception, "
        "源界)——D31 §2 裁定 1：对所有任务生效、地板优先级最高；深史回补=临时调低本键。"
        "批 171d-B）') ON CONFLICT (key) DO NOTHING")
    op.execute(
        "UPDATE sync_config SET retention = '1990-12-19' "
        "WHERE id = 'trade_cal' AND retention IS NULL")


def downgrade() -> None:
    op.execute("DELETE FROM system_config WHERE key = 'sync_floor_default'")
    op.execute(
        "UPDATE sync_config SET retention = NULL "
        "WHERE id = 'trade_cal' AND retention = '1990-12-19'")
