"""删 llm_model_config 种子数据（空 api_key 无意义，用户 Web 自配任意模型）

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-04

- 设计通用：llm_model_config 不限 provider/model，用户 Web 配任意模型（DeepSeek/GLM/OpenAI 兼容/...）
- 删种子（DeepSeek/GLM 空 api_key 行无意义，没 key 的种子不能用）
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 删空 api_key 的种子行（用户 Web 自配，不要预设 DeepSeek/GLM）
    op.execute("DELETE FROM llm_model_config WHERE api_key_encrypted = ''")


def downgrade() -> None:
    # 种子不可逆恢复（用户自配）
    pass
