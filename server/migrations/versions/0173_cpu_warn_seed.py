# EXPAND-CONTRACT: phase=expand pair=None
"""批 171k：alert_cpu_warn seed 值 0.7→0.8（晨窗噪音治理——A-P0-1）。

0112 已 seed 0.7 行且配置行覆盖 _DEF 缺省 ⇒ 改缺省=死代码；须迁移改线上值。
条件守卫（AND value='0.7'）防覆盖运维手改值。晨窗豁免键 `alert_cpu_morning_window`
由代码 `_CFG_KEYS` 缺省 '08:00-09:30'（不 seed——免迁移即可运维覆盖）。

**downgrade**：回 0.7（同样条件守卫）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0173"
down_revision: Union[str, Sequence[str], None] = "0172"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE system_config SET value='0.8' "
               "WHERE key='alert_cpu_warn' AND value='0.7'")


def downgrade() -> None:
    op.execute("UPDATE system_config SET value='0.7' "
               "WHERE key='alert_cpu_warn' AND value='0.8'")
