"""补建 bar_15min/30min/60min/1h/4h 五表（运行时不再 CREATE TABLE IF NOT EXISTS，2026-09-03）

bar_{freq} 是 2026-08-13「运行时不再建表」清零时的例外保留（动态表 ensure_table）。
但 freq 非真动态（固定 8 表：1min/5min/15min/30min/60min/1h/4h/1d），迁移链只建了
bar_1d(0001)/bar_1min/bar_5min(0022)，缺 5 表一直靠运行时 ensure_table 的
CREATE TABLE IF NOT EXISTS 建——违反「Schema 版本管理用 alembic」原则 + 并发
UniqueViolation（隐式复合类型冲突）温床。本迁移补齐，ensure_table 改 verify 后
运行时不再建表。

幂等：has_table 检查，表已存在（历史运行时建过）则跳过。

Revision ID: 0064
Revises: 0063
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0064"
down_revision: str = "0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BAR_TABLES = [
    ("bar_15min", "15min"),
    ("bar_30min", "30min"),
    ("bar_60min", "60min"),
    ("bar_1h", "1H"),   # 与 Freq Literal（schema.py:12）及 bar_1d 的 server_default 口径对齐（大写）
    ("bar_4h", "4H"),
]


def _create_bar_table(table: str, freq: str) -> None:
    """建一张 K 线表（结构同 bar_1d，0022 的 _create_bar_table 同款）。"""
    op.create_table(
        table,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("freq", sa.Text(), nullable=False, server_default=freq),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(), nullable=False),
        sa.Column("high", sa.Numeric(), nullable=False),
        sa.Column("low", sa.Numeric(), nullable=False),
        sa.Column("close", sa.Numeric(), nullable=False),
        sa.Column("volume", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("amount", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("adj_factor", sa.Numeric()),
        sa.Column("source", sa.Text(), nullable=False, server_default="tushare"),
        sa.UniqueConstraint("symbol", "ts"),
    )
    op.create_index(f"idx_{table}_symbol_ts", table, ["symbol", sa.text("ts DESC")])


def upgrade() -> None:
    bind = op.get_bind()
    for table, freq in _BAR_TABLES:
        if not bind.dialect.has_table(bind, table):
            _create_bar_table(table, freq)


def downgrade() -> None:
    for table, _ in reversed(_BAR_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
