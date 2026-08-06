"""live_trading_config 表（实盘分项开关，第二级）

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-03

- live_trading_config 表：按品种分项（convertible/etf/astock/binance_perp/okx_perp），全 false 默认
- 三级 AND 风控：.env ENABLE_LIVE_TRADING（总闸）AND live_trading_config（分项）AND strategy_config.enabled+backtest_verified（策略级）
- A 股股票走 astock 分项（中泰 XTP 通道），和可转债/ETF 一样受开关控制
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "live_trading_config",
        sa.Column("market", sa.Text(), primary_key=True),  # convertible/etf/binance_perp/okx_perp
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    # 种子 5 行全 false（实盘默认全关，需 admin Web 手动开）
    op.execute(
        "INSERT INTO live_trading_config (market, enabled) VALUES "
        "('convertible', false), ('etf', false), ('astock', false), "
        "('binance_perp', false), ('okx_perp', false)"
    )


def downgrade() -> None:
    op.drop_table("live_trading_config")
