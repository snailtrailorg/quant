"""bar_1D/bar_index 单位修复（专家审核 P0，2026-09-07 定性）：Tushare 日线 vol 手/amount 千元 → ×100/×1000 到股/元。

实证（生产 2026-09-07，600000.SH 09-04）：
- bar_1D volume=757659.82（手）vs bar_hub sum(volume)=75522582（股）——×100 吻合
- bar_1D amount=712270.967（千元）=7.12 亿 vs bar_hub sum=7.10 亿——×1000 吻合
与 06 号「回测实盘零迁移切 live」矛盾（schema 对齐、量纲差 100/1000 倍）。

配套代码修复（同批）：TushareAdapter.to_bar_rows + to_save_rows 日线分支 ×100/×1000
（分钟线 stk_mins 本就股/元 不动；bar_1min/bar_5min/bar_hub 不在本迁移范围——本来就对）。

Revision ID: 0068
Revises: 0067
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0068"
down_revision: str = "0067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE bar_1D SET volume = volume * 100, amount = amount * 1000")
    op.execute("UPDATE bar_index SET volume = volume * 100, amount = amount * 1000")


def downgrade() -> None:
    op.execute("UPDATE bar_1D SET volume = volume / 100, amount = amount / 1000")
    op.execute("UPDATE bar_index SET volume = volume / 100, amount = amount / 1000")
