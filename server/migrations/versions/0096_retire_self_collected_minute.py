"""批 64 退役自攒历史分钟线（腾讯攒 + XTP bar_hub 落库）。

用户裁定（2026-09-23）：自攒 A 股历史分钟数据质量存疑、维护成本高，先退役
（断档接受），将来买 Tushare stk_mins（2000 积分/年）正式分钟数据接入。
设计真源 21 号「腾讯攒过渡 + Tushare 终极」——本次提前结束过渡期。

删除：
- minute_symbols 展开表 + minute_data_source 开关（腾讯攒标的清单）
- bar_hub/bar_shadow 影子表（XTP 自攒落库 + diff 对账）
- bar_1min/bar_5min 清空（腾讯攒存量，DELETE 不可逆）
- pools.minute_history_start 列（池级分钟历史标记）

保留：bar_1min/bar_5min 表结构（将来 stk_mins 填）+ 实时行情分发（hub 流）。
破坏性 DDL → 部署走 allow_contract 通道。

Revision ID: 0096
Revises: 0095
"""
from alembic import op
import sqlalchemy as sa

revision = "0096"
down_revision = "0095"


def upgrade() -> None:
    # 攒数据管理面（腾讯攒标的清单 + 数据源开关）
    op.execute("DROP TABLE IF EXISTS minute_symbols")
    op.execute("DELETE FROM system_config WHERE key = 'minute_data_source'")
    # XTP 自攒影子表（bar_hub 落库 + bar_shadow diff 对账）
    op.execute("DROP TABLE IF EXISTS bar_hub")
    op.execute("DROP TABLE IF EXISTS bar_shadow")
    # 清空腾讯攒存量（DELETE 不可逆；表结构保留，将来 stk_mins 填）
    op.execute("DELETE FROM bar_1min")
    op.execute("DELETE FROM bar_5min")
    # 池级分钟历史标记
    op.execute("ALTER TABLE pools DROP COLUMN IF EXISTS minute_history_start")


def downgrade() -> None:
    # 重建表结构 + 恢复配置（bar_1min/bar_5min 的存量数据已 DELETE，不可逆，无法恢复）
    op.execute("ALTER TABLE pools ADD COLUMN IF NOT EXISTS minute_history_start DATE")
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description) VALUES "
        "('minute_data_source', 'tencent', 'string', "
        "'分钟数据源（tencent=腾讯攒过渡 / tushare=Tushare 分钟线终极，互斥单选）') "
        "ON CONFLICT (key) DO NOTHING"
    )
    op.create_table(
        "minute_symbols",
        sa.Column("symbol", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    for name in ("bar_hub", "bar_shadow"):
        op.create_table(
            name,
            sa.Column("symbol", sa.Text(), nullable=False),
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("open", sa.Numeric()),
            sa.Column("high", sa.Numeric()),
            sa.Column("low", sa.Numeric()),
            sa.Column("close", sa.Numeric()),
            sa.Column("volume", sa.Numeric()),
            sa.Column("amount", sa.Numeric(), server_default="0"),
            sa.Column("untrusted", sa.Boolean(), server_default=sa.text("false")),
            sa.PrimaryKeyConstraint("symbol", "ts"),
        )
