"""market_session 配置化：交易时段从硬编码改为 DB 配置驱动（2026-08-24 韧性分层模型）。

不再相信"A 股交易时段 9:31-11:30/13:01-15:00 永不改变"——两周前就踩了
集合竞价/午休边界多次误判；节假日完全没有日历守卫，零 tick 告警会空响。

迁移：建 market_session 表 + 种子数据（A 股/加密永续）。后续加新市场
= INSERT 一行，零代码改动。
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0053"
down_revision: str = "0052"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "market_session",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False, comment="市场名称: A股/加密永续/香港主板"),
        sa.Column("calendar", sa.String(32), nullable=False, server_default="tushare_sse",
                  comment="交易日历源: tushare_sse/tushare_szse/weekday/always/never"),
        sa.Column("session_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'[{\"open\":\"09:30\",\"close\":\"11:30\"},"
                                         "{\"open\":\"13:00\",\"close\":\"15:00\"}]'::jsonb"),
                  comment="交易时段规则 JSON: [{open, close}, ...]，支持跨夜"),
        sa.Column("tz", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_market_session_name"),
    )
    # 种子数据：A 股（Tushare SSE 日历 + 早盘午盘）+ 加密永续（24h）
    op.execute(
        "INSERT INTO market_session (name, calendar, session_rules, tz) VALUES "
        "('A股', 'tushare_sse', '[{\"open\":\"09:31\",\"close\":\"11:30\"},"
        "{\"open\":\"13:01\",\"close\":\"15:00\"}]', 'Asia/Shanghai')"
    )
    op.execute(
        "INSERT INTO market_session (name, calendar, session_rules, tz) VALUES "
        "('加密永续', 'always', '[{\"open\":\"00:00\",\"close\":\"23:59\"}]', 'UTC')"
    )


def downgrade() -> None:
    op.drop_table("market_session")