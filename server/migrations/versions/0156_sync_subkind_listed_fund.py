# EXPAND-CONTRACT: phase=expand  # sub_kind 内部路由词正名（代码-DB 成对改，等价替换）
"""批 127 收尾：sync_kind_config.sub_kind 'etf'→'listed_fund'（全工程 grep 终扫残留①）。

sub_kind 是 adapter.fetch_supply(kind, sub) 的路由词——代码映射（engine
_LITERAL_SUPPLY/`("static_list","etf")` 等）与 DB 行**成对**，本迁移与代码字面量
同批切换，发布即无旧值写入者/读取者。
另：本批同窗完成 fees.py 过时注释清理（canonical_category 别名层已随 Step3 删）
与 strategy 探测词形补全（残留②③——纯代码，零 DB）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0156"
down_revision: Union[str, Sequence[str], None] = "0155"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE sync_kind_config SET sub_kind='listed_fund' "
               "WHERE sub_kind='etf' AND sync_id IN ('listed_fund_list','listed_fund_daily')")
    # sync_log 历史行含 sub_kind? 查过 schema——sync_log 无 sub_kind 列，无需处理


def downgrade() -> None:
    op.execute("UPDATE sync_kind_config SET sub_kind='etf' "
               "WHERE sub_kind='listed_fund' AND sync_id IN ('listed_fund_list','listed_fund_daily')")
