"""D25：能力集 token 迁移（daily/minute/snapshot/quote 四旧词退役，trading 保词）。

存量值映射（D25 §六.4）：
- daily/minute → hist_quote（历史行情）
- snapshot/quote → rt_quote（实时行情）
- trading → 不动（8 处 SQL 域谓词 'trading'=ANY 零改动的立法前提）

存量行预览：tushare{daily,minute}→{hist_quote}、tencent{minute,snapshot}→{rt_quote}
（历史分钟已退役 0096，快照/分时均实时）、xtp/加密/emt{trading,quote}→{trading,rt_quote}。

Revision ID: 0105
Revises: 0104
"""
from alembic import op

revision = "0105"
down_revision = "0104"

_OLD = "'daily','minute','snapshot','quote'"
_NEW = "'hist_quote','rt_quote'"


def upgrade() -> None:
    op.execute(f"""
        UPDATE external_interface SET capabilities = (
            SELECT array_agg(DISTINCT CASE c
                WHEN 'daily' THEN 'hist_quote'
                WHEN 'minute' THEN 'hist_quote'
                WHEN 'snapshot' THEN 'rt_quote'
                WHEN 'quote' THEN 'rt_quote'
                ELSE c END)
            FROM unnest(capabilities) AS c)
        WHERE capabilities && ARRAY[{_OLD}]::text[]
    """)


def downgrade() -> None:
    # 近似恢复（有损注明）：v2 合并去重后原组合不可精确恢复，各还原一个代表词
    op.execute(f"""
        UPDATE external_interface SET capabilities = (
            SELECT array_agg(DISTINCT CASE c
                WHEN 'hist_quote' THEN 'daily'
                WHEN 'rt_quote' THEN 'snapshot'
                ELSE c END)
            FROM unnest(capabilities) AS c)
        WHERE capabilities && ARRAY[{_NEW}]::text[]
    """)
