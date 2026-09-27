"""批 66c：order_log 加 bar_fingerprint JSONB（D26 §3.4④ 回补归因——冷切换/缺口窗下单归因锚）。

ts+OHLCV 随下单落（strategy._last_bar 驱动 bar 记忆）；gen 不入指纹（worker 心跳 gen 维度
+ts 时间窗交叉可溯）。存量行 NULL=历史单无指纹（正常，不回填）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0108"
down_revision = "0107"


def upgrade() -> None:
    op.add_column("order_log", sa.Column("bar_fingerprint", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("order_log", "bar_fingerprint")
