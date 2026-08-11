"""LLM 网关简化：移除 llm_model_config.tier + feishu_config.lang（2026-08-07）

- llm_model_config.tier：死代码（6 调用点全 regular），改 priority 全局主备容灾
- feishu_config.lang：移除语言注入，LLM 按输入语言自然回复

详见 flow/decisions.md 2026-08-07 + 待办 L1/L2。
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("llm_model_config", "tier")
    op.drop_column("feishu_config", "lang")


def downgrade():
    op.add_column("feishu_config", sa.Column("lang", sa.Text(), nullable=True))
    op.add_column("llm_model_config", sa.Column("tier", sa.Text(), nullable=True))
