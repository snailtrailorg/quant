"""data_source_config 表（数据源配置 DB 化，平台化数据层 PT3）

DataSource 接口抽象：别人实现 DataSource 基类接入自己的数据源（Wind/聚宽等）。
当前实现 TushareDataSource（token 从 DB 读，.env fallback）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "data_source_config",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),          # tushare/wind/akshare/...
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text()),              # Fernet 加密（api_key/token）
        sa.Column("params", sa.Text()),                             # JSON: base_url/timeout/retry/proxy
        sa.Column("usage_limit", sa.Integer()),                     # 日限额（如 Tushare 积分）
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_data_source_provider", "data_source_config", ["provider"])


def downgrade():
    op.drop_index("ix_data_source_provider", table_name="data_source_config")
    op.drop_table("data_source_config")
