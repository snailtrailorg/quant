"""D25：能力集 token 迁移（daily/minute/snapshot/quote 四旧词退役，trading 保词）。

存量值映射（D25 §六.4；代码盲审 A-P0 修：tencent 的 minute 先剥离——0090 种子
{minute,snapshot} 且 0096 退役攒分钟时未清列，通用映射会产出越集 {hist_quote,rt_quote}
击穿 ⊆ 立法）：
- daily/minute → hist_quote（历史行情）
- snapshot/quote → rt_quote（实时行情）
- trading → 不动（8 处 SQL 域谓词 'trading'=ANY 零改动的立法前提）
- tencent 专项：先 array_remove(minute) → {rt_quote}（与 docstring 预览一致）

存量行预览：tushare{daily,minute}→{hist_quote}、tencent{minute,snapshot}→{rt_quote}、
xtp/加密/emt{trading,quote}→{trading,rt_quote}。

部署次序约束：须与代码同批部署（Ansible 管道迁移带内先于服务重启）——代码已删
daily/minute 别名，DB 若仍是旧值则 resolve 空 tushare 候选（盲审 B-P2-4）。

Revision ID: 0105
Revises: 0104
"""
from alembic import op

revision = "0105"
down_revision = "0104"

_OLD = "'daily','minute','snapshot','quote'"
_NEW = "'hist_quote','rt_quote'"


def upgrade() -> None:
    # ① tencent 专项：剥离已退役的 minute（0096 退役攒分钟，防止通用映射产出越集 hist_quote）
    op.execute("UPDATE external_interface SET capabilities = array_remove(capabilities, 'minute') "
               "WHERE provider='tencent'")
    # ② 通用映射（provider 无关）
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
    # 近似恢复（有损注明）：v2 合并去重后原组合不可精确恢复，各还原一个代表词；
    # tencent 的 minute 不恢复（0096 已退役该能力，恢复即越集）
    op.execute(f"""
        UPDATE external_interface SET capabilities = (
            SELECT array_agg(DISTINCT CASE c
                WHEN 'hist_quote' THEN 'daily'
                WHEN 'rt_quote' THEN 'snapshot'
                ELSE c END)
            FROM unnest(capabilities) AS c)
        WHERE capabilities && ARRAY[{_NEW}]::text[]
    """)
