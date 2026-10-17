# EXPAND-CONTRACT: phase=expand  # 表正名两步走之 expand：新表名先行、旧表保留可回滚
"""批 126q·expand：场内基金正名第一步——listed_fund_basic 并行立表（用户裁定 2026-10-17）。

概念纠偏：etf_basic_info 实存 Tushare fund_basic(market='E') 全量=**场内基金全集**
（ETF+LOF+封基，2973 行中约 500 只名称含 LOF、239 只主动管理型），窄名宽用。

两步走（表 RENAME 属回滚不安全 op——migration_policy 立法，DDL 门拦截后改走正道）：
- **本步（expand）**：`CREATE TABLE IF NOT EXISTS listed_fund_basic (LIKE ... INCLUDING ALL)`
  + 全量拷贝（幂等 ON CONFLICT）+ sync_config/sync_kind_config pg_table 指向新表；
  旧表 etf_basic_info **原样保留**（回滚=改回 pg_table 即可，旧代码仍可读旧表）。
  同步 handler（_sync_etf_list）随批改写新表——旧表自本版起停更（快照语义，无损）。
- **contract 步（下一发布 0151）**：RENAME etf_basic_info → 归档名 / DROP，pair=0150
  （expand 先行上产的跨发布要求）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0150"
down_revision: Union[str, Sequence[str], None] = "0149"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS listed_fund_basic "
        "(LIKE etf_basic_info INCLUDING ALL)")
    # 全量拷贝（幂等：复跑不重不覆盖——ts_code PK 由 INCLUDING ALL 带入）
    op.execute(
        "INSERT INTO listed_fund_basic SELECT * FROM etf_basic_info "
        "ON CONFLICT (ts_code) DO NOTHING")
    # 三真源指向切换（sync_config/sync_kind_config——对账测试守门）
    op.execute("UPDATE sync_config SET pg_table='listed_fund_basic' "
               "WHERE pg_table='etf_basic_info'")
    op.execute("UPDATE sync_kind_config SET pg_table='listed_fund_basic' "
               "WHERE pg_table='etf_basic_info'")


def downgrade() -> None:
    # expand 可回滚：指回旧表（新表留观或 DROP 皆可——选保守留表，GC 另批）
    op.execute("UPDATE sync_kind_config SET pg_table='etf_basic_info' "
               "WHERE pg_table='listed_fund_basic'")
    op.execute("UPDATE sync_config SET pg_table='etf_basic_info' "
               "WHERE pg_table='listed_fund_basic'")
