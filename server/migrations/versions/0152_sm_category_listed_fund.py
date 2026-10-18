# EXPAND-CONTRACT: phase=expand  # 数据值归一（UPDATE 非破坏——旧值被别名兼容层等价替换）
"""批 127·Step2：SM 台账 category 存量归一 'etf'→'listed_fund'（写入端同批切键）。

为什么数据 UPDATE 可走 expand 相（非 contract）：旧值→新值是**等价替换**——读端
CATEGORY_ALIASES/factor 映射两键等价（Step1 已上产验证），归一后任何路径行为不变；
回滚安全：回退 SQL 把值改回即恢复（无 schema 变化、无数据丢失）。
sync 不可收敛性：SMClient.upsert_rows 的 ON CONFLICT 不更新 category——写入端切键
（engine._sync_etf_list 本批）后既有行仍留旧值，必须显式 UPDATE。
Step3（contract）将清 permission/live_trading_config 旧键行并删代码别名。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0152"
down_revision: Union[str, Sequence[str], None] = "0151"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE security_master SET category='listed_fund', updated_at=now() "
               "WHERE category='etf'")


def downgrade() -> None:
    op.execute("UPDATE security_master SET category='etf', updated_at=now() "
               "WHERE category='listed_fund'")
