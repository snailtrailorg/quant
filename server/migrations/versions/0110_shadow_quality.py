"""批 62b：shadow 对账（A03 §15.2）——shadow_policy 配置表+shadow_diff 结果表+system_config 键种子。

- shadow_policy：kind 级采样配置（预算单位=API 调用次数/月——Tushare 积分是档位非钱包）。
  种子：bar_daily enabled（sample_n=5）；bar_minute 空档（stk_mins 采购 gate，enabled=false）。
- shadow_diff：per-field diff 行（含 __missing__/__extra__ 伪字段）；30 天清理=beat quality_cleanup。
- system_config 键：sm_golden_static_list（62c SM 金标准源，缺省 tushare）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0110"
down_revision = "0109"


def upgrade() -> None:
    op.create_table(
        "shadow_policy",
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("backup_source", sa.Text(), nullable=False, server_default="tushare"),
        sa.Column("sample_n", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("tolerance", sa.JSON(), nullable=True,
                  comment='per-field 容差：{"price": {"abs": 0.01, "rel": 1e-4}, "volume": {"rel": 1e-3}, "amount": {"rel": 1e-3}, "adj_factor": {"rel": 1e-4}}'),
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
        sa.Column("field", sa.Text(), nullable=False,
                  comment="字段名；__missing__=主源缺行/__extra__=主源多行（A-P2-5 伪字段）"),
        sa.Column("main_val", sa.Numeric(), nullable=True),
        sa.Column("backup_val", sa.Numeric(), nullable=True),
        sa.Column("whitelist_hit", sa.Text(), nullable=True,
                  comment="命中的白名单条目 id（NULL=真 diff——超容差告警计数只数 NULL）"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_shadow_diff_kind_date", "shadow_diff", ["kind", "trade_date"])
    op.create_index("ix_shadow_diff_whitelist", "shadow_diff", ["whitelist_hit"])
    op.create_index("ix_shadow_diff_created", "shadow_diff", ["created_at"])
    op.execute("INSERT INTO shadow_policy (kind, backup_source, sample_n, enabled, note) VALUES "
               "('bar_daily', 'tushare', 5, true, '同源自洽对账（A-P1-3 边界：不覆盖源侧系统误差）')")
    op.execute("INSERT INTO shadow_policy (kind, enabled, note) VALUES "
               "('bar_minute', false, '空档：stk_mins 采购 gate（D26 §3.4）——采购后启用')")
    op.execute("INSERT INTO system_config (key, value) VALUES ('sm_golden_static_list', 'tushare') "
               "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    op.execute("DELETE FROM system_config WHERE key='sm_golden_static_list'")
    op.drop_index("ix_shadow_diff_created")
    op.drop_index("ix_shadow_diff_whitelist")
    op.drop_index("ix_shadow_diff_kind_date")
    op.drop_table("shadow_diff")
    op.drop_table("shadow_policy")
