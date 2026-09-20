"""批 56a·M1：SecurityMaster/节奏域/dataset_version（29 号 §四——56a 部分，ts 迁移 56b 另批）。

四表+种子+三表合并回填+九数据表 dataset_version 列。
回填源（金融四审③）：asset_static_info（A股股票——含 list/delist 生命周期列）+
etf_basic_info（ETF）+cb_basic_info（转债）——按品类标 category；
ts_code→vt_symbol 转换（SH/SZ/BJ→SHSE/SZSE/BSE，与 schema.to_vt_symbol 同映射）。

盲审修订（2026-09-20 双盲 A/B）：种子/回填改 exec_driver_sql（根因：op.execute 走编译路径，
JSON 里 ":true" 被解析为绑定参数，原 "\\:true" 是对此的错误转义且漏反斜杠进 PG——两条路都死）；
market_hours 增 anchor 列（day_anchor 表驱动去魔法映射）；回填补生命周期列；
conv_price effective_from 脏值正则防御+list_date 回落。

Revision ID: 0092
Revises: 0091
"""
from alembic import op
import sqlalchemy as sa

revision = "0092"
down_revision = "0091"

# ts_code 后缀 → 交易所（SQL 内联映射；加所两处同改——真源 schema.to_vt_symbol）
_EXCH_CASE = """
  CASE split_part(ts_code, '.', 2)
    WHEN 'SH' THEN 'SHSE' WHEN 'SZ' THEN 'SZSE' WHEN 'BJ' THEN 'BSE'
    ELSE split_part(ts_code, '.', 2) END
"""


def _d(col: str) -> str:
    """脏值安全 date 转换：YYYYMMDD / YYYY-MM-DD 之外（空串/'None'/畸形）→ NULL。"""
    return (f"CASE WHEN {col} ~ '^[0-9]{{4}}-?[0-9]{{2}}-?[0-9]{{2}}$' "
            f"THEN {col}::date ELSE NULL END")


def upgrade() -> None:
    op.create_table(
        "security_master",
        sa.Column("vt_symbol", sa.Text(), primary_key=True),          # '600000.SHSE'
        sa.Column("market", sa.Text(), nullable=False),               # astock|crypto（词典 markets.py）
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),             # stock|etf|convertible|fund|reits|perp
        sa.Column("name", sa.Text()),
        sa.Column("industry", sa.Text()),                              # 现状从 asset_static_info 带
        sa.Column("list_date", sa.Date()), sa.Column("delist_date", sa.Date()),
        sa.Column("multiplier", sa.Numeric(), nullable=False, server_default="1"),   # 股 100 股/手=100；转债 10 张/手=10
        sa.Column("tick_size", sa.Numeric(), nullable=False, server_default="0.01"),
        sa.Column("fee_model_ref", sa.Text()),
        sa.Column("margin_model_ref", sa.Text()),
        sa.Column("session_id", sa.Text(), nullable=False, server_default="astock_main"),
        sa.Column("trade_phase", sa.Text(), nullable=False, server_default="T+1"),   # 转债 T+0/crypto T+0
        sa.Column("routing_hints", sa.dialects.postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "security_state",                                              # 时变属性层（28 §4.1）
        sa.Column("vt_symbol", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),                  # st|limit_band|conv_price|margin_tier
        sa.Column("value", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("vt_symbol", "effective_from", "kind"),
    )
    op.create_table(
        "market_hours",                                                # 节奏域（28 §4.2）
        sa.Column("session_id", sa.Text(), primary_key=True),
        sa.Column("tz", sa.Text(), nullable=False),
        sa.Column("calendar", sa.Text(), nullable=False, server_default="trade_cal"),  # crypto_247=none
        sa.Column("anchor", sa.Time(), nullable=False, server_default="00:00"),        # 日界锚（28 §3.2——astock 15:00/crypto 00:00）
        sa.Column("sessions", sa.dialects.postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "band_rules",                                                  # limit_band 规则表（29 v3 六审——规则派生非同步源）
        sa.Column("exchange", sa.Text(), primary_key=True),
        sa.Column("board", sa.Text(), primary_key=True),               # main|star|chinext|bse
        sa.Column("pct_normal", sa.Numeric(), nullable=False),         # 常规涨跌幅
        sa.Column("pct_st", sa.Numeric(), nullable=False),             # ST 涨跌幅
    )

    # 种子：market_hours 两 session（28 §4.2 时段表——scope 语法 交易所[:板块]）
    op.get_bind().exec_driver_sql("""
      INSERT INTO market_hours (session_id, tz, calendar, anchor, sessions) VALUES
      ('astock_main', '+08:00', 'trade_cal', '15:00', $js$[
        {"phase":"pre","start":"09:15","end":"09:20","cancel":true},
        {"phase":"auction","start":"09:20","end":"09:25","cancel":false},
        {"phase":"open","start":"09:30","end":"11:30"},
        {"phase":"lunch","start":"11:30","end":"13:00"},
        {"phase":"open","start":"13:00","end":"14:57"},
        {"phase":"close_auct","start":"14:57","end":"15:00","scope":"SZSE|BSE|SHSE:STAR"},
        {"phase":"post_fix","start":"15:05","end":"15:30","scope":"SHSE:STAR|SZSE:CHINEXT"}
      ]$js$),
      ('crypto_247', 'UTC', 'none', '00:00', $js$[{"phase":"open","start":"00:00","end":"24:00"}]$js$)
      ON CONFLICT (session_id) DO NOTHING
    """)
    # 种子：band_rules（A股涨跌幅事实——主板10/科创创业20/北交30，ST 折半）
    op.get_bind().exec_driver_sql("""
      INSERT INTO band_rules (exchange, board, pct_normal, pct_st) VALUES
        ('SHSE','main',10,5), ('SHSE','star',20,20),
        ('SZSE','main',10,5), ('SZSE','chinext',20,20),
        ('BSE','bse',30,30)
      ON CONFLICT (exchange, board) DO NOTHING
    """)

    # 三表合并回填 security_master（幂等 ON CONFLICT DO NOTHING——重跑零重复）
    # stock 源=asset_static_info（含 list/delist 生命周期列——static_symbols 无此两列）
    op.get_bind().exec_driver_sql(f"""
      INSERT INTO security_master (vt_symbol, market, exchange, category, name, industry,
                                   list_date, delist_date,
                                   multiplier, tick_size, session_id, trade_phase)
      SELECT split_part(ts_code, '.', 1) || '.' || {_EXCH_CASE}, 'astock', {_EXCH_CASE},
             'stock', name, industry, {_d('list_date')}, {_d('delist_date')},
             100, 0.01, 'astock_main', 'T+1'
      FROM asset_static_info
      WHERE list_status='L' AND split_part(ts_code, '.', 2) IN ('SH','SZ','BJ')
      ON CONFLICT (vt_symbol) DO NOTHING
    """)
    op.get_bind().exec_driver_sql(f"""
      INSERT INTO security_master (vt_symbol, market, exchange, category, name,
                                   list_date,
                                   multiplier, tick_size, session_id, trade_phase)
      SELECT split_part(ts_code, '.', 1) || '.' || {_EXCH_CASE}, 'astock', {_EXCH_CASE},
             'etf', name, {_d('list_date')}, 100, 0.001, 'astock_main', 'T+1'
      FROM etf_basic_info
      ON CONFLICT (vt_symbol) DO NOTHING
    """)
    op.get_bind().exec_driver_sql(f"""
      INSERT INTO security_master (vt_symbol, market, exchange, category, name,
                                   list_date, delist_date,
                                   multiplier, tick_size, session_id, trade_phase)
      SELECT split_part(ts_code, '.', 1) || '.' || {_EXCH_CASE}, 'astock', {_EXCH_CASE},
             'convertible', bond_short_name, {_d('list_date')}, {_d('delist_date')},
             10, 0.001, 'astock_main', 'T+0'
      FROM cb_basic_info
      ON CONFLICT (vt_symbol) DO NOTHING
    """)
    # 转股价下修时变行（conv_price←cb_basic——五源之一；effective_from=转换起始日，
    # 脏值回落发行日 list_date，再兜 2010-01-01——盲审 B：dev 实测存在脏 conv_start_date 行）
    op.get_bind().exec_driver_sql(f"""
      INSERT INTO security_state (vt_symbol, effective_from, kind, value)
      SELECT split_part(ts_code, '.', 1) || '.' || {_EXCH_CASE},
             COALESCE({_d('conv_start_date')}, {_d('list_date')}, DATE '2010-01-01'),
             'conv_price', jsonb_build_object('conv_price', conv_price)
      FROM cb_basic_info WHERE conv_price IS NOT NULL
      ON CONFLICT (vt_symbol, effective_from, kind) DO NOTHING
    """)

    # 九数据表 + daily_basic 加 dataset_version（29 号 §四——PG 11+ ADD COLUMN DEFAULT 元数据操作不重写表）
    for tbl in ("bar_1D", "bar_1min", "bar_5min", "bar_15min", "bar_30min", "bar_60min",
                "bar_1h", "bar_4h", "bar_index", "daily_basic"):
        op.get_bind().exec_driver_sql(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS dataset_version bigint NOT NULL DEFAULT 1")


def downgrade() -> None:
    for tbl in ("bar_1D", "bar_1min", "bar_5min", "bar_15min", "bar_30min", "bar_60min",
                "bar_1h", "bar_4h", "bar_index", "daily_basic"):
        op.get_bind().exec_driver_sql(f"ALTER TABLE {tbl} DROP COLUMN IF EXISTS dataset_version")
    op.drop_table("band_rules")
    op.drop_table("market_hours")
    op.drop_table("security_state")
    op.drop_table("security_master")
