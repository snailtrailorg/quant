"""多账号源 D1：security_master.board 字段化 + venue_permission 表（31 号 §三/§四 + D1）。

board 枚举列（main/star/chinext/bse）从 asset_static_info.market 中文文本归一化回填——
退役 _board_of 前缀判断（688/689→star 等）；exchange 复用已有 security_master.exchange
（SHSE/SZSE/BSE，0092 填充链已落，不新增不重填）。

venue_permission = venue 侧权限存储（D1-2 首决）：venue_id → 允许 category/exchange/board
集合 + is_st 允许布尔（仅主板）+ convertible 权限。perp 分项 gate 由 market_op（role 级）
管，不进 venue_permission。

回填失败模式：market 值不在归一化表 → board 留 NULL（venue_allows 判 fail-closed，宁拒勿错）。

Revision ID: 0097
Revises: 0096
"""
from alembic import op
import sqlalchemy as sa

revision = "0097"
down_revision = "0096"

# ts_code 后缀 → 交易所（与 0092 _EXCH_CASE 同映射；真源 schema.to_vt_symbol）
_EXCH_CASE = """
  CASE split_part(a.ts_code, '.', 2)
    WHEN 'SH' THEN 'SHSE' WHEN 'SZ' THEN 'SZSE' WHEN 'BJ' THEN 'BSE'
    ELSE split_part(a.ts_code, '.', 2) END
"""


def upgrade() -> None:
    # 1. security_master 加 board 列（nullable——NULL=未知，venue_allows fail-closed）
    op.add_column("security_master",
                  sa.Column("board", sa.Text(), nullable=True))

    # 2. 回填 board（仅 category='stock'；market 中文文本归一化，未知留 NULL）
    #    中小板已并入主板（2021 深市合并），002xxx 归 main。
    op.execute(f"""
        UPDATE security_master sm
        SET board = CASE a.market
            WHEN '主板' THEN 'main'
            WHEN '中小板' THEN 'main'
            WHEN '创业板' THEN 'chinext'
            WHEN '科创板' THEN 'star'
            WHEN '北交所' THEN 'bse'
            WHEN 'CDR' THEN 'star'
            ELSE NULL
        END
        FROM asset_static_info a
        WHERE sm.category = 'stock'
          AND sm.vt_symbol = split_part(a.ts_code, '.', 1) || '.' || {_EXCH_CASE}
    """)

    # 3. venue_permission 表（venue 侧权限；FK 级联删——venue 删则权限行同删）
    op.create_table(
        "venue_permission",
        sa.Column("venue_id", sa.BigInteger(),
                  sa.ForeignKey("external_interface.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("allowed_categories", sa.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),   # stock/etf/convertible/fund/reits/perp
        sa.Column("allowed_exchanges", sa.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),   # SHSE/SZSE/BSE（股东户维度）
        sa.Column("allowed_boards", sa.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),   # main/star/chinext/bse
        sa.Column("is_st_allowed", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),          # 仅 board=main 生效
        sa.Column("convertible_allowed", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),          # 可转债权限（10 万+2 年）
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("venue_permission")
    op.drop_column("security_master", "board")
