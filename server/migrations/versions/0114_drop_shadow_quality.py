"""批 79：删除 shadow 对账（同源自检无意义）——DROP shadow_policy / shadow_diff 表。

shadow 行情主备对账（主源 bar_1D vs 备源 Tushare 现拉，底层同源 pro.daily）整体删除，
只保留 SM 对账（sm_reconcile）——sm_golden_static_list 键（62c 金标准源，0110 种子）保留不动。
"""
from alembic import op
import sqlalchemy as sa

revision = "0114"
down_revision = "0113"


def upgrade() -> None:
    op.drop_index("ix_shadow_diff_created", table_name="shadow_diff")
    op.drop_index("ix_shadow_diff_whitelist", table_name="shadow_diff")
    op.drop_index("ix_shadow_diff_kind_date", table_name="shadow_diff")
    op.drop_table("shadow_diff")
    op.drop_table("shadow_policy")


def downgrade() -> None:
    # 删除不可逆（数据不回填）；downgrade 仅重建表壳保证回滚链完整——回滚到批 79 前需重新种子
    op.create_table(
        "shadow_policy",
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("backup_source", sa.Text(), nullable=False, server_default="tushare"),
        sa.Column("sample_n", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("tolerance", sa.JSON(), nullable=True),
        sa.Column("budget_calls_month", sa.Integer(), nullable=False, server_default=sa.text("200")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_table(
        "shadow_diff",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("main_val", sa.Numeric(), nullable=True),
        sa.Column("backup_val", sa.Numeric(), nullable=True),
        sa.Column("whitelist_hit", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_shadow_diff_kind_date", "shadow_diff", ["kind", "trade_date"])
    op.create_index("ix_shadow_diff_whitelist", "shadow_diff", ["whitelist_hit"])
    op.create_index("ix_shadow_diff_created", "shadow_diff", ["created_at"])
