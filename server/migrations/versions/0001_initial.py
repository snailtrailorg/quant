"""initial schema: 10 张业务表

Revision ID: 0001
Revises:
Create Date: 2026-07-31

从 init-schema.sql 转。alembic 用 quant 用户跑，表 owner 自动 = quant（无需 ALTER OWNER）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users（认证）
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="viewer"),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("username"),
    )

    # 2. audit_log（审计日志）
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text()),
        sa.Column("detail", sa.Text()),
        sa.Column("old_value", sa.Text()),
        sa.Column("new_value", sa.Text()),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # 3. sync_config（同步任务配置）
    op.create_table(
        "sync_config",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("tushare_api", sa.Text(), nullable=False),
        sa.Column("pg_table", sa.Text(), nullable=False),
        sa.Column("data_type", sa.Text(), nullable=False),
        sa.Column("sync_mode", sa.Text(), nullable=False, server_default="incremental"),
        sa.Column("schedule", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("last_sync_date", sa.Text()),
        sa.Column("last_sync_ts", sa.DateTime(timezone=True)),
        sa.Column("last_sync_count", sa.Integer(), server_default="0"),
        sa.Column("last_status", sa.Text(), server_default="idle"),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # 4. sync_log（同步日志）
    op.create_table(
        "sync_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("sync_id", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("mode", sa.Text()),
        sa.Column("start_date", sa.Text()),
        sa.Column("end_date", sa.Text()),
        sa.Column("rows_pulled", sa.Integer(), server_default="0"),
        sa.Column("rows_saved", sa.Integer(), server_default="0"),
        sa.Column("duration_ms", sa.Integer(), server_default="0"),
        sa.Column("status", sa.Text(), server_default="running"),
        sa.Column("error", sa.Text()),
        sa.Column("failed_dates", sa.Text()),
        sa.Column("expected_days", sa.Integer()),
        sa.Column("actual_days", sa.Integer()),
    )

    # 5. bar_1d（K线，统一 schema 对齐 XTP）。2026-08-18 #48 修正：原名 "bar_1D" 经
    #    SQLAlchemy 引号化建出大写表，而运行时代码不带引号（PG 折叠小写）——fresh 环境会
    #    双表分裂（链建大写壳+数据流进运行时小写表），服务器 bar_1D 孤儿即此来源
    op.create_table(
        "bar_1d",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("freq", sa.Text(), nullable=False, server_default="1D"),
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
    op.create_index("idx_bar_1d_symbol_ts", "bar_1d", ["symbol", sa.text("ts DESC")])

    # 6. daily_basic（A股基本面）
    op.create_table(
        "daily_basic",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("vt_symbol", sa.Text(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Numeric()),
        sa.Column("turnover_rate", sa.Numeric()),
        sa.Column("pe", sa.Numeric()),
        sa.Column("pe_ttm", sa.Numeric()),
        sa.Column("pb", sa.Numeric()),
        sa.Column("ps", sa.Numeric()),
        sa.Column("ps_ttm", sa.Numeric()),
        sa.Column("dv_ratio", sa.Numeric()),
        sa.Column("dv_ttm", sa.Numeric()),
        sa.Column("total_mv", sa.Numeric()),
        sa.Column("circ_mv", sa.Numeric()),
        sa.UniqueConstraint("ts_code", "trade_date"),
    )
    op.create_index("idx_daily_basic_ts_code", "daily_basic", ["ts_code", sa.text("trade_date DESC")])

    # 7. asset_static_info（A股股票列表）
    op.create_table(
        "asset_static_info",
        sa.Column("ts_code", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("industry", sa.Text()),
        sa.Column("market", sa.Text()),
        sa.Column("list_status", sa.Text()),
        sa.Column("list_date", sa.Text()),
        sa.Column("delist_date", sa.Text()),
    )

    # 8. cb_basic_info（可转债基本信息）
    op.create_table(
        "cb_basic_info",
        sa.Column("ts_code", sa.Text(), primary_key=True),
        sa.Column("bond_short_name", sa.Text()),
        sa.Column("stk_code", sa.Text()),
        sa.Column("stk_short_name", sa.Text()),
        sa.Column("maturity", sa.Text()),
        sa.Column("par", sa.Numeric()),
        sa.Column("issue_price", sa.Numeric()),
        sa.Column("conv_price", sa.Numeric()),
        sa.Column("conv_start_date", sa.Text()),
        sa.Column("conv_end_date", sa.Text()),
        sa.Column("maturity_date", sa.Text()),
        sa.Column("coupon_rate", sa.Numeric()),
        sa.Column("rate_clause", sa.Text()),
        sa.Column("list_date", sa.Text()),
        sa.Column("delist_date", sa.Text()),
    )

    # 9. etf_basic_info（ETF基金列表）
    op.create_table(
        "etf_basic_info",
        sa.Column("ts_code", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("management", sa.Text()),
        sa.Column("fund_type", sa.Text()),
        sa.Column("invest_type", sa.Text()),
        sa.Column("list_date", sa.Text()),
    )

    # 10. trade_cal（交易日历）
    op.create_table(
        "trade_cal",
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("cal_date", sa.Date(), nullable=False),
        sa.Column("is_open", sa.Integer()),
        sa.Column("pretrade_date", sa.Date()),
        sa.PrimaryKeyConstraint("exchange", "cal_date"),
    )


def downgrade() -> None:
    # 反序删除
    op.drop_table("trade_cal")
    op.drop_table("etf_basic_info")
    op.drop_table("cb_basic_info")
    op.drop_table("asset_static_info")
    op.drop_index("idx_daily_basic_ts_code", "daily_basic")
    op.drop_table("daily_basic")
    op.drop_index("idx_bar_1d_symbol_ts", "bar_1d")
    op.drop_table("bar_1d")
    op.drop_table("sync_log")
    op.drop_table("sync_config")
    op.drop_table("audit_log")
    op.drop_table("users")
