"""system_config 系统配置表（key-value，管理员可调，支持动态生效）。

首个配置项：celery_concurrency（Celery worker 并发度，支持运行时动态调整 via app.control.pool_grow/shrink）。

migration 0026：在 0025 之上建 system_config 表 + 种子 celery_concurrency=2。
"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "system_config",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_type", sa.Text(), nullable=False, server_default="string"),  # string/int/float/bool/json
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_by", sa.Text(), nullable=True),
    )
    # 种子：Celery 并发度（默认 2，与原 systemd -c 2 一致）
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description) VALUES "
        "('celery_concurrency', '2', 'int', 'Celery worker 并发度（回测/同步等后台任务并行数，运行时可动态调整）')"
    )


def downgrade():
    op.drop_table("system_config")