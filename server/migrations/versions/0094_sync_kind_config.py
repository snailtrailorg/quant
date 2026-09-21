"""批 58·M3：sync_kind_config + restate_event 两表 + sync_config.provider 可空化（29 号 §六）。

归置表落地：20 sync_id 收编进 sync_kind_config（kind/sub_kind/pg_table/pk_cols/float/text/rebuild）
——真实表名从 handler 代码反推（历史 sync_config.pg_table 有 bar_1D 大写/etf_list 写 asset_static_info
实为 etf_basic_info 的口径漂移，此处归正）。
tushare_api 列退役=58 收编代码同车项（fetch 分派表就位后 drop），本迁移不做——只 provider 可空化。

Revision ID: 0094
Revises: 0093
"""
from alembic import op
import sqlalchemy as sa

revision = "0094"
down_revision = "0093"

# kind 用 text（Python 层 contract.py DataKind Literal 是真源——29 号 §一"映射住代码"；不加 PG CHECK）
# sub_kind：聚合域子类判别（featured_daily 的 moneyflow/margin_detail/top_list/cyq_perf/block_trade；
#           static_list 的 stock/etf/convertible；industry_class 的 concept）
# pk_cols：行为等价对照的排序键（29 号验收①"按 pk 排序"）

_KIND_ROWS = [
    # (sync_id, kind, sub_kind, pg_table, pk_cols, float_cols, text_cols, rebuild)
    ("astock_daily",  "bar_daily", "stock", "bar_1d", ["symbol", "ts"],
     ["open", "high", "low", "close", "volume", "amount"], [], "incremental"),
    ("etf_daily",     "bar_daily", "etf", "bar_1d", ["symbol", "ts"],
     ["open", "high", "low", "close", "volume", "amount"], [], "incremental"),
    ("cb_daily",      "bar_daily", "convertible", "bar_1d", ["symbol", "ts"],
     ["open", "high", "low", "close", "volume", "amount"], [], "incremental"),
    ("index_daily",   "index_daily", None, "bar_index", ["symbol", "ts"],
     ["open", "high", "low", "close", "volume", "amount"], [], "incremental"),
    ("astock_minute", "bar_minute", None, "bar_1min", ["symbol", "ts"],
     ["open", "high", "low", "close", "volume", "amount"], [], "incremental"),
    ("astock_minute_5min", "bar_minute", None, "bar_5min", ["symbol", "ts"],
     ["open", "high", "low", "close", "volume", "amount"], [], "incremental"),
    ("astock_basic",  "fundamental_daily", None, "daily_basic", ["ts_code", "trade_date"],
     ["close", "turnover_rate", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
      "total_mv", "circ_mv"], [], "incremental"),
    ("astock_list",   "static_list", "stock", "asset_static_info", ["ts_code"],
     [], ["name", "industry", "market", "list_status", "list_date", "delist_date"], "full_rebuild"),
    ("etf_list",      "static_list", "etf", "etf_basic_info", ["ts_code"],
     [], ["name", "management", "fund_type", "invest_type", "list_date"], "full_rebuild"),
    ("cb_basic",      "static_list", "convertible", "cb_basic_info", ["ts_code"],
     ["par", "issue_price", "conv_price", "coupon_rate"],
     ["bond_short_name", "stk_code", "stk_short_name", "maturity", "conv_start_date", "conv_end_date",
      "maturity_date", "rate_clause", "list_date", "delist_date"], "full_rebuild"),
    ("stk_limit_sync", "stk_limit", None, "stk_limit", ["trade_date", "ts_code"],
     ["pre_close", "up_limit", "down_limit"], [], "incremental"),
    ("moneyflow_sync", "featured_daily", "moneyflow", "moneyflow", ["ts_code", "trade_date"],
     ["buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount", "buy_md_vol", "buy_md_amount",
      "sell_md_vol", "sell_md_amount", "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
      "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount", "net_mf_vol", "net_mf_amount"],
     [], "incremental"),
    ("margin_detail_sync", "featured_daily", "margin_detail", "margin_detail", ["trade_date", "ts_code"],
     ["rzye", "rqye", "rzmre", "rqyl", "rzche", "rqchl", "rqmcl", "rzrqye"], [], "incremental"),
    ("top_list_sync", "featured_daily", "top_list", "top_list", ["trade_date", "ts_code"],
     ["close", "pct_change", "turnover_rate", "amount", "l_sell", "l_buy", "l_amount", "net_amount",
      "net_rate", "amount_rate", "float_values"], ["name", "reason"], "incremental"),
    ("block_trade_sync", "featured_daily", "block_trade", "block_trade", ["ts_code", "trade_date"],
     ["price", "vol", "amount"], ["buyer", "seller"], "incremental"),
    ("cyq_perf_sync", "featured_daily", "cyq_perf", "cyq_perf", ["ts_code", "trade_date"],
     ["his_low", "his_high", "cost_5pct", "cost_15pct", "cost_50pct", "cost_85pct", "cost_95pct",
      "weight_avg", "winner_rate"], [], "incremental"),
    ("forecast_sync", "financial_stmt", "forecast", "forecast", ["ts_code", "ann_date", "end_date"],
     ["p_change_min", "p_change_max", "net_profit_min", "net_profit_max", "last_parent_net"],
     ["type", "summary", "change_reason", "first_ann_date"], "incremental"),
    ("namechange_sync", "static_list", None, "namechange", ["ts_code", "name", "start_date"],
     [], ["name", "start_date", "end_date", "ann_date", "change_reason"], "full_rebuild"),
    ("concept_sync", "industry_class", "concept", "concept", ["ts_code"],
     [], ["name"], "full_rebuild"),
    ("trade_cal",    "trade_cal", None, "trade_cal", ["exchange", "cal_date"],
     [], ["is_open", "pretrade_date"], "full_rebuild"),
]


def _arr(vals):
    """text[] 字面量（PG 数组）。"""
    return "{" + ",".join(f'"{v}"' for v in vals) + "}"


def upgrade() -> None:
    op.create_table(
        "sync_kind_config",
        sa.Column("sync_id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("sub_kind", sa.Text()),
        sa.Column("pg_table", sa.Text(), nullable=False),
        sa.Column("pk_cols", sa.dialects.postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("float_cols", sa.dialects.postgresql.ARRAY(sa.Text()), server_default="{}"),
        sa.Column("text_cols", sa.dialects.postgresql.ARRAY(sa.Text()), server_default="{}"),
        sa.Column("rebuild", sa.Text(), nullable=False, server_default="incremental"),
        sa.CheckConstraint("rebuild IN ('incremental','full_rebuild')", name="ck_sync_kind_rebuild"),
    )
    op.create_table(
        "restate_event",                # 28 §3.4 修订事件化（PIT 载体——M7/R6 消费）
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("sub_kind", sa.Text()),
        sa.Column("symbols", sa.dialects.postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),      # 披露日（PIT 锚）
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # 种子：20 sync_id 归置（真实表名——历史 pg_table 口径漂移归正；代码项 3 行此前无 sync_config 行）
    bind = op.get_bind()
    for sync_id, kind, sub, tbl, pk, fc, tc, rebuild in _KIND_ROWS:
        bind.exec_driver_sql(
            "INSERT INTO sync_kind_config (sync_id, kind, sub_kind, pg_table, pk_cols, float_cols, text_cols, rebuild) "
            "VALUES (%s, %s, %s, %s, %s::text[], %s::text[], %s::text[], %s) "
            "ON CONFLICT (sync_id) DO NOTHING",
            (sync_id, kind, sub, tbl, _arr(pk), _arr(fc), _arr(tc), rebuild))

    # sync_config.provider 可空化（58 收编后由 resolve(supply) 选源——不再单行配置）
    op.alter_column("sync_config", "provider", nullable=True)


def downgrade() -> None:
    op.alter_column("sync_config", "provider", nullable=False)
    op.drop_table("restate_event")
    op.drop_table("sync_kind_config")
