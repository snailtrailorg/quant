"""三档数据架构第一档：全局定时同步表（U 审修订版，2026-08-19）

9 张表：stk_limit / moneyflow / margin_detail / top_list / block_trade / cyq_perf / forecast / namechange / concept
+ sync_config 种子（错峰 cron + trade_day_filter + soft_time_limit 覆盖由 engine handler 侧处理）
+ data_sync_scheduler 周期 1800s→300s

设计依据：docs/reference/tushare/api-docs/_实测参考手册.md（41 接口实测列结构）

Revision ID: 0045
Revises: 0044
Create Date: 2026-08-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0045"
down_revision: str = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. stk_limit 每日涨跌停价格（盘前 08:40 Tushare 更新，实测确认） ──
    op.create_table("stk_limit",
        sa.Column("trade_date", sa.Text(), nullable=False),
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("pre_close", sa.Numeric()),
        sa.Column("up_limit", sa.Numeric(), nullable=False),
        sa.Column("down_limit", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("trade_date", "ts_code"),
    )
    op.create_index("idx_stk_limit_code", "stk_limit", ["ts_code", "trade_date"])

    # ── 2. moneyflow 个股资金流向（盘后，20 列大中小单） ──
    op.create_table("moneyflow",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("trade_date", sa.Text(), nullable=False),
        # 小单
        sa.Column("buy_sm_vol", sa.Numeric()), sa.Column("buy_sm_amount", sa.Numeric()),
        sa.Column("sell_sm_vol", sa.Numeric()), sa.Column("sell_sm_amount", sa.Numeric()),
        # 中单
        sa.Column("buy_md_vol", sa.Numeric()), sa.Column("buy_md_amount", sa.Numeric()),
        sa.Column("sell_md_vol", sa.Numeric()), sa.Column("sell_md_amount", sa.Numeric()),
        # 大单
        sa.Column("buy_lg_vol", sa.Numeric()), sa.Column("buy_lg_amount", sa.Numeric()),
        sa.Column("sell_lg_vol", sa.Numeric()), sa.Column("sell_lg_amount", sa.Numeric()),
        # 特大单
        sa.Column("buy_elg_vol", sa.Numeric()), sa.Column("buy_elg_amount", sa.Numeric()),
        sa.Column("sell_elg_vol", sa.Numeric()), sa.Column("sell_elg_amount", sa.Numeric()),
        # 净流入
        sa.Column("net_mf_vol", sa.Numeric()),
        sa.Column("net_mf_amount", sa.Numeric()),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
    )
    op.create_index("idx_moneyflow_date", "moneyflow", ["trade_date"])

    # ── 3. margin_detail 融资融券明细（T+1） ──
    op.create_table("margin_detail",
        sa.Column("trade_date", sa.Text(), nullable=False),
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("rzye", sa.Numeric()),      # 融资余额
        sa.Column("rqye", sa.Numeric()),      # 融券余额
        sa.Column("rzmre", sa.Numeric()),     # 融资买入额
        sa.Column("rqyl", sa.Numeric()),      # 融券余量
        sa.Column("rzche", sa.Numeric()),     # 融资偿还额
        sa.Column("rqchl", sa.Numeric()),     # 融券偿还量
        sa.Column("rqmcl", sa.Numeric()),     # 融券卖出量
        sa.Column("rzrqye", sa.Numeric()),    # 融资融券余额
        sa.PrimaryKeyConstraint("trade_date", "ts_code"),
    )
    op.create_index("idx_margin_code", "margin_detail", ["ts_code", "trade_date"])

    # ── 4. top_list 龙虎榜（盘后 17-18 点） ──
    op.create_table("top_list",
        sa.Column("trade_date", sa.Text(), nullable=False),
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("close", sa.Numeric()),
        sa.Column("pct_change", sa.Numeric()),
        sa.Column("turnover_rate", sa.Numeric()),
        sa.Column("amount", sa.Numeric()),
        sa.Column("l_sell", sa.Numeric()),
        sa.Column("l_buy", sa.Numeric()),
        sa.Column("l_amount", sa.Numeric()),
        sa.Column("net_amount", sa.Numeric()),
        sa.Column("net_rate", sa.Numeric()),
        sa.Column("amount_rate", sa.Numeric()),
        sa.Column("float_values", sa.Numeric()),
        sa.Column("reason", sa.Text()),
        sa.PrimaryKeyConstraint("trade_date", "ts_code"),
    )

    # ── 5. block_trade 大宗交易（盘后 17 点） ──
    op.create_table("block_trade",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("trade_date", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric()),
        sa.Column("vol", sa.Numeric()),
        sa.Column("amount", sa.Numeric()),
        sa.Column("buyer", sa.Text()),
        sa.Column("seller", sa.Text()),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
    )

    # ── 6. cyq_perf 每日筹码及胜率（盘后） ──
    op.create_table("cyq_perf",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("trade_date", sa.Text(), nullable=False),
        sa.Column("his_low", sa.Numeric()),
        sa.Column("his_high", sa.Numeric()),
        sa.Column("cost_5pct", sa.Numeric()),
        sa.Column("cost_15pct", sa.Numeric()),
        sa.Column("cost_50pct", sa.Numeric()),
        sa.Column("cost_85pct", sa.Numeric()),
        sa.Column("cost_95pct", sa.Numeric()),
        sa.Column("weight_avg", sa.Numeric()),
        sa.Column("winner_rate", sa.Numeric()),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
    )
    op.create_index("idx_cyq_perf_date", "cyq_perf", ["trade_date"])

    # ── 7. forecast 业绩预告（按公告日增量） ──
    op.create_table("forecast",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("ann_date", sa.Text(), nullable=False),
        sa.Column("end_date", sa.Text(), nullable=False),
        sa.Column("type", sa.Text()),
        sa.Column("p_change_min", sa.Numeric()),
        sa.Column("p_change_max", sa.Numeric()),
        sa.Column("net_profit_min", sa.Numeric()),
        sa.Column("net_profit_max", sa.Numeric()),
        sa.Column("last_parent_net", sa.Numeric()),
        sa.Column("first_ann_date", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("change_reason", sa.Text()),
        sa.PrimaryKeyConstraint("ts_code", "ann_date", "end_date"),
    )

    # ── 8. namechange 股票曾用名（ST 识别数据源） ──
    op.create_table("namechange",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("start_date", sa.Text()),
        sa.Column("end_date", sa.Text()),
        sa.Column("ann_date", sa.Text()),
        sa.Column("change_reason", sa.Text()),
        sa.PrimaryKeyConstraint("ts_code", "name", "start_date"),
    )

    # ── 9. concept 概念板块 ──
    op.create_table("concept",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("ts_code"),
    )

    # ── sync_config 种子（错峰 cron + trade_day_filter） ──
    op.execute("""
        INSERT INTO sync_config (id, name, tushare_api, pg_table, data_type, sync_mode, schedule, trade_day_filter, enabled, description) VALUES
        ('stk_limit_sync',      '每日涨跌停价格',   'pro.stk_limit',      'stk_limit',      'astock', 'incremental', '0 18 * * 1-5', 'trade_day', 'true', '全市场涨跌停价，盘前 08:40 出，18:00 拉当日'),
        ('moneyflow_sync',      '个股资金流向',     'pro.moneyflow',      'moneyflow',      'astock', 'incremental', '30 16 * * 1-5', 'trade_day', 'true', '大中小单买卖+净流入（20 列），盘后'),
        ('margin_detail_sync',  '融资融券明细',     'pro.margin_detail',  'margin_detail',  'astock', 'incremental', '0 9 * * 1-5',  'trade_day', 'true', 'T+1，次日 09:00 拉昨日'),
        ('top_list_sync',       '龙虎榜每日明细',   'pro.top_list',       'top_list',       'astock', 'incremental', '0 18 * * 1-5',  'trade_day', 'true', '盘后 17-18 点'),
        ('block_trade_sync',    '大宗交易',         'pro.block_trade',    'block_trade',    'astock', 'incremental', '15 18 * * 1-5', 'trade_day', 'true', '盘后 17 点，错峰 18:15'),
        ('cyq_perf_sync',       '每日筹码及胜率',   'pro.cyq_perf',       'cyq_perf',       'astock', 'incremental', '30 18 * * 1-5', 'trade_day', 'true', '筹码汇总统计，盘后，错峰 18:30'),
        ('forecast_sync',       '业绩预告',         'pro.forecast',       'forecast',       'astock', 'incremental', '0 8 * * 1-5',  'none',     'true', '按公告日增量（每日检查新披露）'),
        ('namechange_sync',     '股票曾用名',       'pro.namechange',     'namechange',     'astock', 'full',        '0 6 * * 1',    'none',     'true', '每周一全量重建（ST 识别）'),
        ('concept_sync',        '概念板块',         'pro.concept',        'concept',        'astock', 'full',        '0 7 * * 1',    'none',     'true', '每周一全量重建')
        ON CONFLICT (id) DO NOTHING
    """)

    # ── data_sync_scheduler 周期 1800s→300s（U-4：300s 粒度才对得上 cron 窗口） ──
    # 注意：这个在 scheduler/app.py 代码层改，不是数据库


def downgrade() -> None:
    for t in ["stk_limit", "moneyflow", "margin_detail", "top_list", "block_trade",
               "cyq_perf", "forecast", "namechange", "concept"]:
        op.execute(f"DROP TABLE IF EXISTS {t}")
    op.execute("DELETE FROM sync_config WHERE id LIKE '%_sync' AND id IN "
               "('stk_limit_sync','moneyflow_sync','margin_detail_sync','top_list_sync',"
               "'block_trade_sync','cyq_perf_sync','forecast_sync','namechange_sync','concept_sync')")
