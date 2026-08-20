"""三档数据第二档：池内 per-symbol 深度数据表（U 审 2026-08-19）

10 张表：income / balancesheet / cashflow / fina_indicator / cyq_chips /
top10_holders / dividend / pledge_stat / share_float / stk_holdernumber
+ 独立池数据同步 beat 任务（不同于已禁用的 pool_minute_sync）

设计依据：docs/reference/tushare/api-docs/_实测参考手册.md
表结构 = Tushare 原始列名（全列存，渲染层选核心列展示）

Revision ID: 0046
Revises: 0045
Create Date: 2026-08-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0046"
down_revision: str = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 财务三表 + 财务指标（全列存，PK 含 ann_date 处理重述） ──
    _sa_numeric_cols = lambda cols: [sa.Column(c, sa.Numeric()) for c in cols]
    _sa_text_cols = lambda cols: [sa.Column(c, sa.Text()) for c in cols]

    # income 利润表（85 列——选核心列 + 全列预留 JSON）
    op.create_table("income",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("ann_date", sa.Text(), nullable=False),
        sa.Column("end_date", sa.Text(), nullable=False),
        sa.Column("report_type", sa.Text()),
        # 核心列
        sa.Column("total_revenue", sa.Numeric()),
        sa.Column("revenue", sa.Numeric()),
        sa.Column("total_profit", sa.Numeric()),
        sa.Column("n_income", sa.Numeric()),
        sa.Column("n_income_attr_p", sa.Numeric()),
        sa.Column("basic_eps", sa.Numeric()),
        sa.Column("diluted_eps", sa.Numeric()),
        sa.Column("rd_exp", sa.Numeric()),
        # 全列 JSON（保完整性，渲染层选核心）
        sa.Column("raw_json", sa.Text()),
        sa.PrimaryKeyConstraint("ts_code", "ann_date", "end_date"),
    )
    op.create_index("idx_income_code", "income", ["ts_code", "end_date"])

    # balancesheet 资产负债表（核心列 + JSON）
    op.create_table("balancesheet",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("ann_date", sa.Text(), nullable=False),
        sa.Column("end_date", sa.Text(), nullable=False),
        sa.Column("report_type", sa.Text()),
        sa.Column("total_assets", sa.Numeric()),
        sa.Column("total_cur_assets", sa.Numeric()),
        sa.Column("total_nca", sa.Numeric()),
        sa.Column("total_liab", sa.Numeric()),
        sa.Column("total_cur_liab", sa.Numeric()),
        sa.Column("total_ncl", sa.Numeric()),
        sa.Column("total_hldr_eqy_exc_min_int", sa.Numeric()),
        sa.Column("money_cap", sa.Numeric()),
        sa.Column("goodwill", sa.Numeric()),
        sa.Column("raw_json", sa.Text()),
        sa.PrimaryKeyConstraint("ts_code", "ann_date", "end_date"),
    )
    op.create_index("idx_bs_code", "balancesheet", ["ts_code", "end_date"])

    # cashflow 现金流量表（核心列 + JSON）
    op.create_table("cashflow",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("ann_date", sa.Text(), nullable=False),
        sa.Column("end_date", sa.Text(), nullable=False),
        sa.Column("report_type", sa.Text()),
        sa.Column("n_cashflow_act", sa.Numeric()),
        sa.Column("n_cashflow_inv_act", sa.Numeric()),
        sa.Column("n_cash_flows_fnc_act", sa.Numeric()),
        sa.Column("net_profit", sa.Numeric()),
        sa.Column("c_fr_sale_sg", sa.Numeric()),
        sa.Column("free_cashflow", sa.Numeric()),
        sa.Column("raw_json", sa.Text()),
        sa.PrimaryKeyConstraint("ts_code", "ann_date", "end_date"),
    )
    op.create_table("fina_indicator",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("ann_date", sa.Text(), nullable=False),
        sa.Column("end_date", sa.Text(), nullable=False),
        # 核心指标
        sa.Column("eps", sa.Numeric()),
        sa.Column("roe", sa.Numeric()),
        sa.Column("roa", sa.Numeric()),
        sa.Column("gross_margin", sa.Numeric()),
        sa.Column("netprofit_margin", sa.Numeric()),
        sa.Column("current_ratio", sa.Numeric()),
        sa.Column("quick_ratio", sa.Numeric()),
        sa.Column("debt_to_assets", sa.Numeric()),
        sa.Column("assets_turn", sa.Numeric()),
        sa.Column("revenue_ps", sa.Numeric()),
        sa.Column("bps", sa.Numeric()),
        sa.Column("ocfps", sa.Numeric()),
        sa.Column("roe_yearly", sa.Numeric()),
        sa.Column("netprofit_yoy", sa.Numeric()),
        sa.Column("revenue_yoy", sa.Numeric()),
        sa.Column("raw_json", sa.Text()),
        sa.PrimaryKeyConstraint("ts_code", "ann_date", "end_date"),
    )
    op.create_index("idx_fina_ind_code", "fina_indicator", ["ts_code", "end_date"])

    # ── cyq_chips 筹码分布（per-symbol per-day per-price 档位） ──
    op.create_table("cyq_chips",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("trade_date", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("percent", sa.Numeric()),
        sa.PrimaryKeyConstraint("ts_code", "trade_date", "price"),
    )
    op.create_index("idx_cyq_chips_date", "cyq_chips", ["trade_date"])

    # ── top10_holders / top10_floatholders ──
    op.create_table("top10_holders",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("ann_date", sa.Text(), nullable=False),
        sa.Column("end_date", sa.Text(), nullable=False),
        sa.Column("holder_name", sa.Text()),
        sa.Column("hold_amount", sa.Numeric()),
        sa.Column("hold_ratio", sa.Numeric()),
        sa.Column("hold_float_ratio", sa.Numeric()),
        sa.Column("hold_change", sa.Numeric()),
        sa.Column("holder_type", sa.Text()),
        sa.PrimaryKeyConstraint("ts_code", "ann_date", "end_date", "holder_name"),
    )

    # ── dividend 分红送股 ──
    op.create_table("dividend",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("end_date", sa.Text(), nullable=False),
        sa.Column("ann_date", sa.Text()),
        sa.Column("div_proc", sa.Text()),
        sa.Column("stk_div", sa.Text()),
        sa.Column("cash_div", sa.Numeric()),
        sa.Column("cash_div_tax", sa.Numeric()),
        sa.Column("record_date", sa.Text()),
        sa.Column("ex_date", sa.Text()),
        sa.Column("pay_date", sa.Text()),
        sa.PrimaryKeyConstraint("ts_code", "end_date", "div_proc"),
    )

    # ── pledge_stat 股权质押统计 ──
    op.create_table("pledge_stat",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("end_date", sa.Text(), nullable=False),
        sa.Column("pledge_count", sa.Numeric()),
        sa.Column("unrest_pledge", sa.Numeric()),
        sa.Column("rest_pledge", sa.Numeric()),
        sa.Column("total_share", sa.Numeric()),
        sa.Column("pledge_ratio", sa.Numeric()),
        sa.PrimaryKeyConstraint("ts_code", "end_date"),
    )

    # ── share_float 限售解禁 ──
    op.create_table("share_float",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("ann_date", sa.Text()),
        sa.Column("float_date", sa.Text(), nullable=False),
        sa.Column("float_share", sa.Numeric()),
        sa.Column("float_ratio", sa.Numeric()),
        sa.Column("holder_name", sa.Text()),
        sa.Column("share_type", sa.Text()),
        sa.PrimaryKeyConstraint("ts_code", "float_date"),
    )

    # ── stk_holdernumber 股东人数 ──
    op.create_table("stk_holdernumber",
        sa.Column("ts_code", sa.Text(), nullable=False),
        sa.Column("ann_date", sa.Text(), nullable=False),
        sa.Column("end_date", sa.Text(), nullable=False),
        sa.Column("holder_num", sa.Numeric()),
        sa.PrimaryKeyConstraint("ts_code", "end_date"),
    )


def downgrade() -> None:
    for t in ["income", "balancesheet", "cashflow", "fina_indicator", "cyq_chips",
               "top10_holders", "dividend", "pledge_stat", "share_float", "stk_holdernumber"]:
        op.execute(f"DROP TABLE IF EXISTS {t}")
