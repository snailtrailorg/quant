# EXPAND-CONTRACT: phase=contract（RENAME——0149 同窗无依赖，独立可回滚）
"""批 126q：etf_basic_info → listed_fund_basic（正名——用户裁定 2026-10-17）。

概念纠偏：本表存的是 Tushare `fund_basic(market='E')` 全量=**场内基金全集**
（ETF+LOF+封基，2973 行中约 500 只名称含 LOF、239 只主动管理型），表名 etf 名不副实
（窄名宽用——ETF 只是场内基金的子集）。改名零数据迁移（RENAME 即时），配套：
- sync_config.pg_table 'etf_basic_info' 行同步 UPDATE（kind='etf_list' 的真源指向）；
- 代码引用（engine.py/stock.py）随批改；
- 市场维度键 'etf'（ASSET_CATEGORIES/SM category/permission）**不在本批**——注册键
  另立大批（炸半径横跨交易风控，C 档挂账）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0150"
down_revision: Union[str, Sequence[str], None] = "0149"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE etf_basic_info RENAME TO listed_fund_basic")
    op.execute("UPDATE sync_config SET pg_table='listed_fund_basic' "
               "WHERE pg_table='etf_basic_info'")
    # sync_kind_config 归置表也存 pg_table（三真源之一——测试双真源对账抓出的漏网，
    # test_batch107_supply_literals::test_pg_table_single_source 守门）
    op.execute("UPDATE sync_kind_config SET pg_table='listed_fund_basic' "
               "WHERE pg_table='etf_basic_info'")


def downgrade() -> None:
    op.execute("UPDATE sync_kind_config SET pg_table='etf_basic_info' "
               "WHERE pg_table='listed_fund_basic'")
    op.execute("UPDATE sync_config SET pg_table='etf_basic_info' "
               "WHERE pg_table='listed_fund_basic'")
    op.execute("ALTER TABLE listed_fund_basic RENAME TO etf_basic_info")
