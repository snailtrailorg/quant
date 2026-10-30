"""批 158：bar 表滚动保留——sync_config.retention_days 列 + 三表 ts 单列索引。

- retention_days INTEGER NULL：滚动保留窗（天）。NULL=否决票（该表任一共写档 NULL => 整表不裁）。
  与 retention（DATE 起点地板=拉多老）语义正交。
- ts 索引（CONCURRENTLY，0060 先例形态）：DELETE ... WHERE source=%s AND ts<... 无前导索引
  会全表扫（现有 idx_{t}_symbol_ts 是 symbol 前导——A 审 P1）。
- 缺省值 UPDATE：binance 1min=7/15min=60/1h=180（方案批158 v2 §2.2；暖机窗随保留窗 7 天
  已用户裁定 2026-10-30）。okx 未启用档 NULL（启用时随 binance 同裁定）。

Revision ID: 0164
Revises: 0163
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0164"
down_revision: Union[str, Sequence[str], None] = "0163"
branch_labels: Union[str, Sequence[str], str, None] = None
depends_on: Union[str, Sequence[str], None] = None

_IDX = [("bar_1min", "idx_bar_1min_ts", "ts"),
        ("bar_15min", "idx_bar_15min_ts", "ts"),
        ("bar_1h", "idx_bar_1h_ts", "ts")]

_DEFAULTS = [("binance_perp_1min", 7), ("binance_perp_15min", 60), ("binance_perp_hourly", 180)]


def upgrade() -> None:
    op.add_column("sync_config", sa.Column("retention_days", sa.Integer(), nullable=True))
    for sid, days in _DEFAULTS:
        op.execute(f"UPDATE sync_config SET retention_days={days} WHERE id='{sid}'")
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        for tbl, idx, col in _IDX:
            if bind.dialect.has_table(bind, tbl):
                op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {idx} ON {tbl} ({col})")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for tbl, idx, col in _IDX:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {idx}")
    op.drop_column("sync_config", "retention_days")
