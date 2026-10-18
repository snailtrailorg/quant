# EXPAND-CONTRACT: phase=expand  # sync id 数据值归一（等价替换——代码字面量同批全切，无旧 id 写入者）
"""批 127 深水区：同步任务 id 正名 etf_list/etf_daily → listed_fund_list/listed_fund_daily。

用户裁定（2026-10-18）：内部标识也要统一——运维/开发面混淆同罪。
范围与判定：
- sync_config PK 行改值 + sync_kind_config.sync_id 同步（两表键一致是 _LITERAL_SUPPLY
  对账测试的守门面）；
- sync_log/sync_gap **历史行保留旧值**（历史事实记录——审计链语义，用户既有裁定：
  旧数据不改写）；查询面按新 id 直查（gap 对账 DISTINCT 后新窗口走新 id）；
- 代码字面量（engine/_HANDLERS、markets DATA_KINDS、adapters 映射、tasks 启动清单、
  前端 PER_SYMBOL_IDS）同批切换——发布即无旧 id 写入者，无双读期必要。
data_type 列删除另行 contract（回滚不安全 op 独立两步走）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0155"
down_revision: Union[str, Sequence[str], None] = "0154"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MAP = [("etf_list", "listed_fund_list"), ("etf_daily", "listed_fund_daily")]


def upgrade() -> None:
    for old, new in _MAP:
        op.execute(f"UPDATE sync_config SET id='{new}' WHERE id='{old}'")
        op.execute(f"UPDATE sync_kind_config SET sync_id='{new}' WHERE sync_id='{old}'")


def downgrade() -> None:
    for old, new in _MAP:
        op.execute(f"UPDATE sync_kind_config SET sync_id='{old}' WHERE sync_id='{new}'")
        op.execute(f"UPDATE sync_config SET id='{old}' WHERE id='{new}'")
