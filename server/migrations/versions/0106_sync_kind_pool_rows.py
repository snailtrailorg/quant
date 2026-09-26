"""批 64a：sync_kind_config 增量行（D25 §七能力层对齐收尾）。

pool_data 10 项有实现（0046 建表 + POOL_DATA_TYPES 在拉）无登记 + namechange 补 sub_kind——
经新迁移增改行数据，不改 0094 文件（§七立法：表机制不动，行数据经新迁移）。

- sync_id=表名裸词（trade_cal 先例；与 pg_table/sub_kind 同名，SYNC_ID_CAP_MAP 键随同）
- float/text_cols=0046 DDL 全列按型归类（Numeric→float / Text→text），pk_cols 除外；
  raw_json 不入（存储机制非业务列）
- rebuild 与 pool_data 拉取行为一致：财务四表 incremental=True（游标窗口）；cyq_chips
  trade_date=当日退化窗口；top10_holders/dividend/pledge_stat/share_float/stk_holdernumber
  无 incremental 标志=每轮全量 upsert → full_rebuild
- analyst_expect 不落行（裁定）：report_rc 无实现无 pg_table，落行=登记死数据违反 0094
  「真实表名」立法；随 report_rc 接入时加行（D25 §八推迟逻辑）
- top10_floatholders 不登记：0046 未建表（仅注释提及），无实现

Revision ID: 0106
Revises: 0105
"""
from alembic import op

revision = "0106"
down_revision = "0105"

_NEW_ROWS = [
    # (sync_id, kind, sub_kind, pg_table, pk_cols, float_cols, text_cols, rebuild)
    ("income",         "financial_stmt", "income", "income", ["ts_code", "ann_date", "end_date"],
     ["total_revenue", "revenue", "total_profit", "n_income", "n_income_attr_p",
      "basic_eps", "diluted_eps", "rd_exp"], ["report_type"], "incremental"),
    ("balancesheet",   "financial_stmt", "balancesheet", "balancesheet", ["ts_code", "ann_date", "end_date"],
     ["total_assets", "total_cur_assets", "total_nca", "total_liab", "total_cur_liab",
      "total_ncl", "total_hldr_eqy_exc_min_int", "money_cap", "goodwill"], ["report_type"], "incremental"),
    ("cashflow",       "financial_stmt", "cashflow", "cashflow", ["ts_code", "ann_date", "end_date"],
     ["n_cashflow_act", "n_cashflow_inv_act", "n_cash_flows_fnc_act", "net_profit",
      "c_fr_sale_sg", "free_cashflow"], ["report_type"], "incremental"),
    ("fina_indicator", "financial_stmt", "fina_indicator", "fina_indicator", ["ts_code", "ann_date", "end_date"],
     ["eps", "roe", "roa", "gross_margin", "netprofit_margin", "current_ratio",
      "quick_ratio", "debt_to_assets", "assets_turn", "revenue_ps", "bps", "ocfps",
      "roe_yearly", "netprofit_yoy", "revenue_yoy"], [], "incremental"),
    ("cyq_chips",      "featured_daily", "cyq_chips", "cyq_chips", ["ts_code", "trade_date", "price"],
     ["percent"], [], "incremental"),
    ("top10_holders",  "holder_structure", "top10_holders", "top10_holders",
     ["ts_code", "ann_date", "end_date", "holder_name"],
     ["hold_amount", "hold_ratio", "hold_float_ratio", "hold_change"], ["holder_type"], "full_rebuild"),
    ("dividend",       "holder_structure", "dividend", "dividend", ["ts_code", "end_date", "div_proc"],
     ["cash_div", "cash_div_tax"], ["ann_date", "stk_div", "record_date", "ex_date", "pay_date"],
     "full_rebuild"),
    ("pledge_stat",    "holder_structure", "pledge_stat", "pledge_stat", ["ts_code", "end_date"],
     ["pledge_count", "unrest_pledge", "rest_pledge", "total_share", "pledge_ratio"], [], "full_rebuild"),
    ("share_float",    "holder_structure", "share_float", "share_float", ["ts_code", "float_date"],
     ["float_share", "float_ratio"], ["ann_date", "holder_name", "share_type"], "full_rebuild"),
    ("stk_holdernumber", "holder_structure", "stk_holdernumber", "stk_holdernumber", ["ts_code", "end_date"],
     ["holder_num"], ["ann_date"], "full_rebuild"),
]


def _arr(vals):
    """text[] 字面量（PG 数组，同 0094）。"""
    return "{" + ",".join(f'"{v}"' for v in vals) + "}"


def upgrade() -> None:
    bind = op.get_bind()
    for sync_id, kind, sub, tbl, pk, fc, tc, rebuild in _NEW_ROWS:
        bind.exec_driver_sql(
            "INSERT INTO sync_kind_config (sync_id, kind, sub_kind, pg_table, pk_cols, float_cols, text_cols, rebuild) "
            "VALUES (%s, %s, %s, %s, %s::text[], %s::text[], %s::text[], %s) "
            "ON CONFLICT (sync_id) DO NOTHING",
            (sync_id, kind, sub, tbl, _arr(pk), _arr(fc), _arr(tc), rebuild))
    # namechange 补 sub_kind 单列（D25 §七：static_list 家族 stock/etf/convertible/namechange）
    op.execute("UPDATE sync_kind_config SET sub_kind='namechange' "
               "WHERE sync_id='namechange_sync' AND sub_kind IS NULL")


def downgrade() -> None:
    op.execute("UPDATE sync_kind_config SET sub_kind=NULL "
               "WHERE sync_id='namechange_sync' AND sub_kind='namechange'")
    op.execute("DELETE FROM sync_kind_config WHERE sync_id IN ("
               + ",".join(f"'{r[0]}'" for r in _NEW_ROWS) + ")")
