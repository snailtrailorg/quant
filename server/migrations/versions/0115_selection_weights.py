"""批 81 P2-9：选股权重配置化——seed system_config selection_weights 键（现值 2.0/1.0/1.5/1.5）。

选股器（astock_analysis）权重从代码硬编码迁到 DB（system_config 键），读侧 fallback 硬编码现值
保证行为不变。Web 配置面后续按需加（键值 JSON，改权重=改这一行）。
"""
from alembic import op

revision = "0115"
down_revision = "0114"


def upgrade() -> None:
    op.execute(
        "INSERT INTO system_config (key, value) VALUES "
        "('selection_weights', '{\"net_mf_pct\": 2.0, \"lg_flow_pct\": 1.0, \"winner_rate\": 1.5, \"ma_dev\": 1.5}') "
        "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    op.execute("DELETE FROM system_config WHERE key='selection_weights'")
