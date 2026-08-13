"""consolidate runtime tables: 10 张运行时表

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-13

从各 handler 中提取的运行时表，统一迁移管理。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. account_snapshot（账户快照/盈亏）
    op.create_table(
        "account_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("total_value", sa.Numeric(), nullable=False),
        sa.Column("daily_pnl", sa.Numeric(), server_default="0"),
        sa.Column("initial_capital", sa.Numeric(), nullable=False, server_default="1000000"),
    )

    # 2. accounts（交易所账户，含加密 API key/secret）
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text()),
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("api_key_enc", sa.Text()),
        sa.Column("api_secret_enc", sa.Text()),
        sa.Column("api_key_hint", sa.Text()),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # 3. alert_history（告警历史）
    op.create_table(
        "alert_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("level", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("body", sa.Text()),
        sa.Column("channel", sa.Text()),
    )

    # 4. astock_analysis（A股分析结果）
    op.create_table(
        "astock_analysis",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("symbol", sa.Text()),
        sa.Column("action", sa.Text()),
        sa.Column("score", sa.Numeric()),
        sa.Column("rating", sa.Text()),
        sa.Column("factors", sa.JSONB()),
    )

    # 5. broker_usage（券商调用统计）
    op.create_table(
        "broker_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("provider", sa.Text()),
        sa.Column("action", sa.Text()),
        sa.Column("symbol", sa.Text()),
        sa.Column("success", sa.Boolean()),
        sa.Column("latency_ms", sa.Integer()),
    )

    # 6. convertible_terms（可转债条款）
    op.create_table(
        "convertible_terms",
        sa.Column("ts_code", sa.Text(), primary_key=True),
        sa.Column("terms", sa.JSONB()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # 7. signal_log（策略信号）
    op.create_table(
        "signal_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("strategy_id", sa.Text()),
        sa.Column("symbol", sa.Text()),
        sa.Column("action", sa.Text()),
        sa.Column("score", sa.Numeric()),
        sa.Column("price", sa.Numeric()),
    )

    # 8. order_log（订单）
    op.create_table(
        "order_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("strategy_id", sa.Text()),
        sa.Column("symbol", sa.Text()),
        sa.Column("action", sa.Text()),
        sa.Column("volume", sa.Integer()),
        sa.Column("price", sa.Numeric()),
        sa.Column("status", sa.Text(), server_default="submitted"),
        sa.Column("signal_id", sa.BigInteger()),
    )

    # 9. trade_log（成交）
    op.create_table(
        "trade_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("order_id", sa.BigInteger()),
        sa.Column("symbol", sa.Text()),
        sa.Column("action", sa.Text()),
        sa.Column("volume", sa.Integer()),
        sa.Column("price", sa.Numeric()),
        sa.Column("commission", sa.Numeric()),
    )

    # 10. static_symbols（静态标的列表）
    op.create_table(
        "static_symbols",
        sa.Column("ts_code", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("industry", sa.Text()),
        sa.Column("list_status", sa.Text()),
        sa.Column("delisted", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    # 反序删除所有 10 张表
    op.drop_table("static_symbols")
    op.drop_table("trade_log")
    op.drop_table("order_log")
    op.drop_table("signal_log")
    op.drop_table("convertible_terms")
    op.drop_table("broker_usage")
    op.drop_table("astock_analysis")
    op.drop_table("alert_history")
    op.drop_table("accounts")
    op.drop_table("account_snapshot")