# EXPAND-CONTRACT: phase=expand pair=None
"""批 171c 腿 1：suspend_d_sync retention 地板放宽 2026-10-01 → 2014-01-01。

任务＝`flow/任务/批171c-收敛面板.md`（腿 1：suspend 历史回补）。0168 的 retention='2026-10-01'
是「防首跑全史」的保守起步地板；171c 裁定 suspend 事实全史保留（历史停牌窗是面板净化
DEF-10 的主数据源——prod partial 2486 只中 1591 只缺口 >100 日，主因历史长停/暂停上市窗
无净化数据）。suspend_d 上游实测 2014 年起覆盖（2020-06-01 含 000670 暂停上市在案；
2014-02-15 返回 0 行——更早期覆盖不全，回补自然空窗，宁多报不漏报）。

**为什么 2014**：上游最早可回补窗（2013 及以前 API 0 行）；~3000 交易日 × ≤48 行/日
≈ 10 万行级，量可控。**增量调度安全**：last_sync_date 已在 20261106，地板只影响
「无游标首跑/回补夹取」，已推游标的增量轮不受影响。

**downgrade**：retention 回 0168 原值 '2026-10-01'（数据不删——suspend_calendar 行
保留，地板只影响窗口计算）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0169"
down_revision: Union[str, Sequence[str], None] = "0168"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE sync_config SET retention = '2014-01-01' "
        "WHERE id = 'suspend_d_sync' AND retention = '2026-10-01'")


def downgrade() -> None:
    op.execute(
        "UPDATE sync_config SET retention = '2026-10-01' WHERE id = 'suspend_d_sync'")
