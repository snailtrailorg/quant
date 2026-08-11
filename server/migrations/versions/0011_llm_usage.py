"""LLM 用量日志 llm_usage 表（架构 §8，补 P2.10 轻量替代 Prometheus）

gateway._log_usage 每次调 LLM 写一条（provider/model/tokens/latency/success/caller）。
Web 后台读做成本/健康度看板。CREATE TABLE IF NOT EXISTS 兜底，migration 正式建表 + 索引。
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("provider", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("input_tokens", sa.Integer(), server_default="0"),
        sa.Column("output_tokens", sa.Integer(), server_default="0"),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("success", sa.Boolean()),
        sa.Column("error_type", sa.Text()),
        sa.Column("caller", sa.Text()),
    )
    op.create_index("ix_llm_usage_ts", "llm_usage", ["ts"])
    op.create_index("ix_llm_usage_caller", "llm_usage", ["caller"])


def downgrade():
    op.drop_index("ix_llm_usage_caller", table_name="llm_usage")
    op.drop_index("ix_llm_usage_ts", table_name="llm_usage")
    op.drop_table("llm_usage")
