"""批 61·M6：trade_switch_session 表（账号切换状态机载体，A04 §九主规格）。

五态：nominated→confirmed→done / expired（超时回旧链）/ aborted（人工取消）。
半自动立法：不自动切换；唯一索引挡 to 侧活跃双会话（from 侧靠 execute 实时复评兜底）。

A04 sketch 外差异注记：
- 账号列 BIGINT=对齐 external_interface.id BigInteger（0090:90 实据）
- FK RESTRICT=live_task.account_id 同款先例（0100:32）
- extended_at=四审金融⑤「支持续时一次」的落地记账（NULL=未续）
- CHECK (from<>to)=防自切无意义会话

Revision ID: 0107
Revises: 0106
"""
from alembic import op
import sqlalchemy as sa

revision = "0107"
down_revision = "0106"


def upgrade() -> None:
    op.create_table(
        "trade_switch_session",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("from_account", sa.BigInteger(), nullable=False),
        sa.Column("to_account", sa.BigInteger(), nullable=False),
        sa.Column("info_pack", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("checklist", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("nominated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.Column("extended_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('nominated','confirmed','done','expired','aborted')",
                           name="ck_trade_switch_state"),
        sa.CheckConstraint("from_account <> to_account", name="ck_trade_switch_not_self"),
        sa.ForeignKeyConstraint(["from_account"], ["external_interface.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_account"], ["external_interface.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ux_trade_switch_active", "trade_switch_session", ["to_account"],
        unique=True, postgresql_where=sa.text("state IN ('nominated','confirmed')"))
    op.execute("COMMENT ON TABLE trade_switch_session IS "
               "'M6 账号切换会话：半自动立法（A04 §九）——提名信息包人审+检查单实时复评+原子切换'")


def downgrade() -> None:
    op.drop_table("trade_switch_session")
