# EXPAND-CONTRACT: phase=expand  # 市场键正名三步走之 Step1：permission 双值 + 代码别名（SM 不动）
"""批 127·Step1：市场维度键 'etf'→'listed_fund' expand 相（用户裁定 2026-10-18 三步走）。

概念：'etf' 键窄名宽用（实为场内基金全集——A/B 档已正名表/文案；C 档收注册键）。

分层事实（决定各层手段）：
- **permission**（composite unique 含 resource）：本步**复制** resource='listed_fund' 行
  （2 行，subject/effect 保真）——market_op fail-closed，新键先有权限行再切判定。
- **SM 台账**（PK=vt_symbol）：**不可双行**——本步零 SQL，代码读端做两键等价
  （CATEGORY_ALIASES：etf≡listed_fund），写入端切值留 Step2 一次 UPDATE。
- **注册表**（ASSET_CATEGORIES/MARKET_OP_KEYS/_VALID_CATEGORIES）：加新键——
  加法即兼容（旧键还在）。
三步走全景：Step2=写入端切新键+SM UPDATE 归一；Step3=删旧键+别名（contract）。
幂等：ON CONFLICT DO NOTHING；downgrade 删新值行（旧值未动）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0151"
down_revision: Union[str, Sequence[str], None] = "0150"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 权限行：market_op/etf → market_op/listed_fund（subject/effect 保真复制；
    # composite unique (subject_type,subject_id,dimension,resource,effect) 兜幂等）
    op.execute(
        "INSERT INTO permission (dimension, resource, subject_type, subject_id, effect) "
        "SELECT dimension, 'listed_fund', subject_type, subject_id, effect "
        "FROM permission WHERE dimension='market_op' AND resource='etf' "
        "ON CONFLICT DO NOTHING")
    # 实盘开关行：live_trading_config/etf → listed_fund（enabled 保真复制——
    # is_live_trading_allowed 查键失败=fail-closed 锁，新键须先有行）
    op.execute(
        "INSERT INTO live_trading_config (market, enabled) "
        "SELECT 'listed_fund', enabled FROM live_trading_config "
        "WHERE market='etf' "
        "ON CONFLICT (market) DO NOTHING")


def downgrade() -> None:
    op.execute("DELETE FROM live_trading_config WHERE market='listed_fund'")
    op.execute("DELETE FROM permission WHERE dimension='market_op' "
               "AND resource='listed_fund'")
