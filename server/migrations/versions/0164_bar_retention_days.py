"""批 158：bar 表滚动保留——sync_config.retention_days 列 + 三表 ts 单列索引。

- retention_days INTEGER NULL：滚动保留窗（天）。NULL=该档自己的行不裁（共写表按
  source 隔离互不可见——无表级否决，veto 设计 2026-10-31 删除）。
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

# 步4盲审 P0-1（A）：okx 盘中三档共写同三张 bar 表——须同配（NULL 档自己的行不裁，但共写表各档独立生效）。
# 2026-10-31 用户裁定：1min 缺省 7→1 天（磁盘 94% 容量压力——1min 是耗盘主力，滚动只留当日）。
# 15min/60 天、1h/180 天不变。astock_minute=NULL（自己的行不裁）。
_DEFAULTS = [("binance_perp_1min", 1), ("binance_perp_15min", 60), ("binance_perp_hourly", 180),
             ("okx_perp_1min", 1), ("okx_perp_15min", 60), ("okx_perp_hourly", 180)]


def upgrade() -> None:
    # 步4盲审（B-P1-3）：autocommit_block 会先提交挂起事务——CONCURRENTLY 若失败，重跑须
    # 幂等。条件加列 + UPDATE 带 IS NULL 守卫（重跑不覆盖已改值）。
    bind = op.get_bind()
    # prod 教训（2026-10-30 首上产炸点）：SQLAlchemy 2.x 的 bind.execute 不收裸字符串
    # （ObjectNotExecutableError）——须 text() 包裹（0060 的 op.execute 形态不受影响）。
    from sqlalchemy import text as _text
    has_col = bind.execute(_text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='sync_config' AND column_name='retention_days'")).fetchone()
    if not has_col:
        op.add_column("sync_config", sa.Column("retention_days", sa.Integer(), nullable=True))
    for sid, days in _DEFAULTS:
        op.execute(f"UPDATE sync_config SET retention_days={days} "
                   f"WHERE id='{sid}' AND retention_days IS NULL")
    with op.get_context().autocommit_block():
        for tbl, idx, col in _IDX:
            if bind.dialect.has_table(bind, tbl):
                op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {idx} ON {tbl} ({col})")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for tbl, idx, col in _IDX:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {idx}")
    op.drop_column("sync_config", "retention_days")
