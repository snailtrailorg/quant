"""llm_model_config 表（LLM 多模型配置，DB 化）

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-03

- llm_model_config 表：多模型配置（不限 provider），API key Fernet 加密
- 弃 config.yaml 模型配置 + .env DEEPSEEK_*/GLM_*，配置管理 DB 化
- 路由：tier（regular/complex/embedding）+ priority（同 tier 内优先级，小=优先，主备）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_model_config",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),                  # 显示名
        sa.Column("provider", sa.Text(), nullable=False),               # deepseek/glm/openai-compatible/...，不限种类
        sa.Column("model", sa.Text(), nullable=False),                  # deepseek-v4-flash/glm-5.2/...
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),      # Fernet 加密（crypto_utils.encrypt）
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=False, server_default="32768"),
        sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("tier", sa.Text(), nullable=False),                   # regular/complex/embedding
        sa.Column("priority", sa.Integer(), nullable=False, server_default="10"),  # 同 tier 内优先级，小=优先
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("idx_llm_model_tier_priority", "llm_model_config", ["tier", "priority"])
    # 种子：DeepSeek + GLM（enabled=false，待 Admin 填 key 启用；api_key_encrypted 空待填）
    op.execute(
        "INSERT INTO llm_model_config (name, provider, model, api_key_encrypted, base_url, context_window, supports_tools, tier, priority, enabled) VALUES "
        "('DeepSeek V4 Flash', 'deepseek', 'deepseek-v4-flash', '', 'https://api.deepseek.com/v1', 32768, true, 'regular', 10, false), "
        "('DeepSeek V4 Pro',   'deepseek', 'deepseek-v4-pro',   '', 'https://api.deepseek.com/v1', 65536, true, 'complex', 10, false), "
        "('GLM 5.2',           'glm',      'glm-5.2',           '', 'https://open.bigmodel.cn/api/paas/v4', 128000, true, 'regular', 20, false)"
    )


def downgrade() -> None:
    op.drop_index("idx_llm_model_tier_priority", "llm_model_config")
    op.drop_table("llm_model_config")
