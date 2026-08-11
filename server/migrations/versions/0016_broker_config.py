"""broker_config 表（交易通道配置 DB 化，平台化交易层 PT5）。

Broker 接口抽象：别人实现 Broker 接入自己的券商/交易所（IB/CTP 等）。
当前实现 XTP/Binance/OKX（凭证 DB 化，ExecutionAdapter 已有交易接口）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "broker_config",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),          # xtp/binance/okx/ib/ctp/...
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text()),              # Fernet 加密 JSON（app_id/app_secret 或 api_key/api_secret）
        sa.Column("params", sa.Text()),                             # JSON: 服务器地址/超时/代理
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_broker_provider", "broker_config", ["provider"])


def downgrade():
    op.drop_index("ix_broker_provider", table_name="broker_config")
    op.drop_table("broker_config")
