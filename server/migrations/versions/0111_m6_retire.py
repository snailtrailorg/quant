# EXPAND-CONTRACT: legacy reason="批 70；建表(0107)与退役同批"
"""批 70：M6 账号切换退役（D26 账号级拓扑下切换语义不成立——2026-09-27 用户裁定）。

- drop trade_switch_session（0107 建）+drop routing_policy.trade_switch_confirm_timeout_s（0093 建）
- 存量治愈（P0-1）：批 68 终态回写无历史回填的欠账——submitted∧有成交的滞留行按量推导终态
  （量足→all_traded/不足→partial），当日窗前的历史单出告警口径。
"""
from alembic import op
import sqlalchemy as sa

revision = "0111"
down_revision = "0110"


def upgrade() -> None:
    # 存量治愈：成交齐量→all_traded；有成交不足量→partial；无成交滞留 submitted 保持（真实未成交/待撤历史）
    op.execute("""
        UPDATE order_log o SET status='all_traded'
        WHERE o.status IN ('submitted','submitting') AND o.volume IS NOT NULL
        AND o.volume <= coalesce((SELECT sum(t.volume) FROM trade_log t WHERE t.order_id=o.id), 0)
    """)
    op.execute("""
        UPDATE order_log o SET status='partial'
        WHERE o.status IN ('submitted','submitting')
        AND EXISTS (SELECT 1 FROM trade_log t WHERE t.order_id=o.id)
    """)
    op.drop_table("trade_switch_session")
    op.drop_column("routing_policy", "trade_switch_confirm_timeout_s")


def downgrade() -> None:
    op.add_column("routing_policy",
                  sa.Column("trade_switch_confirm_timeout_s", sa.Integer(), nullable=False,
                            server_default=sa.text("300")))
    op.create_table(
        "trade_switch_session",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("from_account", sa.Integer(), sa.ForeignKey("external_interface.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("to_account", sa.Integer(), sa.ForeignKey("external_interface.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("info_pack", sa.JSON()),
        sa.Column("checklist", sa.JSON()),
        sa.Column("nominal_actor", sa.Text(), nullable=False),
        sa.Column("nominated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.Column("extended_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('nominated','confirmed','done','aborted','expired')", name="ck_trade_switch_state"),
        sa.CheckConstraint("from_account <> to_account", name="ck_trade_switch_not_self"),
    )
    # 部分唯一索引（0107 原：active 态 to_account 唯一）
    op.create_index("ux_trade_switch_active", "trade_switch_session", ["to_account"],
                    unique=True, postgresql_where=sa.text("state IN ('nominated','confirmed')"))
