"""schema 收编与漂移修复（#48，L 审修正案 F-B/审计裁定 2026-08-18）

- strategy_config 收编进迁移链（F-B：42 个迁移无一定义它——全新库 upgrade head 不建此表，
  灾备重建即策略 API/runner/飞书/调度团灭；照 0027 if_not_exists 模式，形状=当前代码实际
  读写的 12 列，不含本地遗留 pool_id/account_id/created_at）
- accounts 补 0027 缺失的加密列（服务器旧表先于 0027 存在 → create_table if_not_exists 跳过
  → crypto_utils 的 api_key_enc 列缺失；审计 diff 实锤）
- llm_model_config 列名对齐（服务器列=api_key_enc（前 alembic 旧名）≠ 链/代码 api_key_encrypted
  → LLM 模型加载静默空转；条件 RENAME 幂等，已对齐环境 no-op）

Revision ID: 0042
Revises: 0041
Create Date: 2026-08-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0042"
down_revision: str = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. strategy_config 收编（12 列=代码实际读写形状；已存在环境 no-op）
    op.create_table(
        "strategy_config",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text()),
        sa.Column("adapter", sa.Text()),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("factors", sa.Text()),
        sa.Column("aggregator", sa.Text()),
        sa.Column("risk", sa.Text()),
        sa.Column("params", sa.Text()),
        sa.Column("backtest_verified", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        if_not_exists=True,
    )

    # 2. accounts 补加密列（0027 在旧表存在时被跳过的缺口）
    op.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS api_key_enc TEXT")
    op.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS api_secret_enc TEXT")

    # 3. llm_model_config 列名对齐（条件 RENAME，幂等）
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='llm_model_config' AND column_name='api_key_enc')
               AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_name='llm_model_config' AND column_name='api_key_encrypted')
            THEN
                ALTER TABLE llm_model_config RENAME COLUMN api_key_enc TO api_key_encrypted;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # 收编表不 drop（数据表）；列名不回退（旧名是缺陷不是特性）。
    # 注意：accounts 的 DROP COLUMN 会销毁已存密钥——降级是破坏性操作，按 alembic 对称惯例保留
    # 但生产降级前必须先导出 accounts 凭证
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS api_key_enc")
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS api_secret_enc")
