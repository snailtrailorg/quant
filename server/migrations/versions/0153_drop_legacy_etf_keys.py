# EXPAND-CONTRACT: phase=contract pair=0151
"""批 127·Step3：清 market_op/live_trading_config 旧键行（contract——pair=0151 expand 已上产）。

同批 B 档收尾：`etf_basic_info` 旧表 RENAME 归档（其 expand=0150 已上产——listed_fund_basic
并行立表+三真源切换均已在产验证）。DROP 旧表（含 RENAME 前置）属回滚不安全 op 集，
两步走 contract 正道。
前置事实（上产窗口内已验证）：
- permission：listed_fund 行 2 条生效（profile market_op 六键双 True）；
- live_trading_config：listed_fund 行 enabled=True 生效（etf 折叠不展示）；
- SM：0152 已归一 2973 行，无 category='etf' 残留；
- listed_fund_basic：2973 行在库，sync pg_table 已指新表。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0153"
down_revision: Union[str, Sequence[str], None] = "0152"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM permission WHERE dimension='market_op' AND resource='etf'")
    op.execute("DELETE FROM live_trading_config WHERE market='etf'")
    # B 档收尾：旧表归档名（保留数据一窗供查，物理 DROP 留后续 GC 批）
    op.execute("ALTER TABLE etf_basic_info RENAME TO etf_basic_info_archived_0153")


def downgrade() -> None:
    op.execute("ALTER TABLE etf_basic_info_archived_0153 RENAME TO etf_basic_info")
    # 旧行回补（从 listed_fund 行反复制——Step1 的逆操作）
    op.execute(
        "INSERT INTO permission (dimension, resource, subject_type, subject_id, effect) "
        "SELECT dimension, 'etf', subject_type, subject_id, effect "
        "FROM permission WHERE dimension='market_op' AND resource='listed_fund' "
        "ON CONFLICT DO NOTHING")
    op.execute(
        "INSERT INTO live_trading_config (market, enabled) "
        "SELECT 'etf', enabled FROM live_trading_config WHERE market='listed_fund' "
        "ON CONFLICT (market) DO NOTHING")
