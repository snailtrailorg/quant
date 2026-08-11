"""bar_1min / bar_5min 分钟线表（A1 分钟线管线）。

stk_mins per-symbol 拉取 -> to_save_rows_min -> save_bars(freq)。
表结构与 bar_1D 对齐（统一 schema，回测/实盘一致，零迁移）。

migration 编号：0017 head 之上建 0022（design doc 预留 A1=0022）。
后续 B2/D5/C6/A4 的 0018-0021 建时 down_revision 指向当前 head（0022）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0017"
branch_labels = None
depends_on = None


def _create_bar_table(table: str, freq: str) -> None:
    """建一张 K 线表（结构同 bar_1D）。"""
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
    _create_bar_table("bar_1min", "1min")
    _create_bar_table("bar_5min", "5min")


def downgrade() -> None:
    op.drop_index("idx_bar_5min_symbol_ts", table_name="bar_5min")
    op.drop_table("bar_5min")
    op.drop_index("idx_bar_1min_symbol_ts", table_name="bar_1min")
    op.drop_table("bar_1min")
