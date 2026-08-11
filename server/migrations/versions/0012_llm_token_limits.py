"""LLM token 限制：加 max_input_tokens（程序控制输入上限）+ 改名 max_tokens -> max_output_tokens（API 输出参数）

- max_input_tokens：输入上限，gateway 估算 token + 截断（程序控制，不传 API）
- max_output_tokens：输出上限，gateway 传 API max_tokens（chat.completions 标准参数名）
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("llm_model_config", sa.Column("max_input_tokens", sa.Integer(), nullable=True))
    # 改名 max_tokens -> max_output_tokens（PostgreSQL RENAME COLUMN）
    op.execute("ALTER TABLE llm_model_config RENAME COLUMN max_tokens TO max_output_tokens")


def downgrade():
    op.execute("ALTER TABLE llm_model_config RENAME COLUMN max_output_tokens TO max_tokens")
    op.drop_column("llm_model_config", "max_input_tokens")
