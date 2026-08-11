"""data_source_usage 表（A4 #36）：数据源调用量监控。

DataSource.record_usage 写入；web_api 端点读（用量看板）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0022"  # A1=0022 是当前 head
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "data_source_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("api_name", sa.String(length=64)),
        sa.Column("calls", sa.Integer(), server_default="1"),
        sa.Column("success", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("latency_ms", sa.Integer()),
    )
    op.create_index("ix_data_source_usage_provider_ts", "data_source_usage", ["provider", "ts"])


def downgrade():
    op.drop_index("ix_data_source_usage_provider_ts", table_name="data_source_usage")
    op.drop_table("data_source_usage")
