"""批 130（期二）：sync_kind_config 扩 overlap_days/lag_trade_days 两列（UPDATE 型首例）。

参数从代码常量迁族表（值表按**真实消费点反推**——步 2 双盲审定案：
- overlap=3：_TIER1_BATCH 恰 8 行（engine.py:3040 消费）；
- overlap=7：pool 增量四表 income/balancesheet/cashflow/fina_indicator
  （pool_data.py:134 唯一消费；forecast_sync 同 kind 但走 tier1 工厂=3，holder 五行
  非增量窗口恒 (None,None) 零消费 ⇒ NULL）；
- lag=1：仅 margin_detail_sync（批 92 T+1）；
- 其余 30 行 NULL＝零消费（NULL→读点缺省 0=现状，死值禁种——接读点即激活成行为）。
幂等守卫：ADD COLUMN IF NOT EXISTS + UPDATE…WHERE col IS NULL（0139 范式，防重跑
覆盖运维手改）；DML 内联字面量（--sql 离线渲染安全）。
downgrade：两列 DROP（纯参数列，行零损）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0160"
down_revision: Union[str, Sequence[str], None] = "0159"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OVERLAP3 = ("stk_limit_sync", "moneyflow_sync", "margin_detail_sync", "top_list_sync",
             "block_trade_sync", "cyq_perf_sync", "forecast_sync", "st_list_sync")
_OVERLAP7 = ("income", "balancesheet", "cashflow", "fina_indicator")


def upgrade() -> None:
    op.execute("ALTER TABLE sync_kind_config ADD COLUMN IF NOT EXISTS overlap_days integer")
    op.execute("ALTER TABLE sync_kind_config ADD COLUMN IF NOT EXISTS lag_trade_days integer")
    for sid in _OVERLAP3:
        op.execute(f"UPDATE sync_kind_config SET overlap_days = 3 "
                   f"WHERE sync_id = '{sid}' AND overlap_days IS NULL")
    for sid in _OVERLAP7:
        op.execute(f"UPDATE sync_kind_config SET overlap_days = 7 "
                   f"WHERE sync_id = '{sid}' AND overlap_days IS NULL")
    op.execute("UPDATE sync_kind_config SET lag_trade_days = 1 "
               "WHERE sync_id = 'margin_detail_sync' AND lag_trade_days IS NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE sync_kind_config DROP COLUMN IF EXISTS overlap_days")
    op.execute("ALTER TABLE sync_kind_config DROP COLUMN IF EXISTS lag_trade_days")
