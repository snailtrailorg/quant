"""多账号源 D1 补：venue_permission 存量交易 venue 种子（写路径最小交付——空表接线即全拒）。

给现有 astock 交易 venue 插默认权限（全 A股品种/沪深北/全板块；is_st 关、可转债开——
可转债 10万+2年通常已开，ST 需风险警示权限另开）。crypto venue 加密 gate 未开，暂不种子。
新建 venue 走 PUT 端点显式配权限（权限人工配置哲学）。

downgrade 为 no-op：种子行与人工配置行同形，无法区分——保守不删（防误删人工配置）；
重放靠 ON CONFLICT DO NOTHING 幂等。

Revision ID: 0098
Revises: 0097
"""
from alembic import op

revision = "0098"
down_revision = "0097"


def upgrade() -> None:
    op.execute("""
        INSERT INTO venue_permission
            (venue_id, allowed_categories, allowed_exchanges, allowed_boards,
             is_st_allowed, convertible_allowed)
        SELECT id,
               ARRAY['stock','etf','convertible','fund','reits']::text[],
               ARRAY['SHSE','SZSE','BSE']::text[],
               ARRAY['main','star','chinext','bse']::text[],
               false, true
        FROM external_interface
        WHERE 'trading' = ANY(capabilities) AND market = 'astock'
        ON CONFLICT (venue_id) DO NOTHING
    """)


def downgrade() -> None:
    # 种子行无独立标识（与人工配置同形），保守 no-op 不删——防误删人工配置
    pass
