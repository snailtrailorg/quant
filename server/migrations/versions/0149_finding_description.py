# EXPAND-CONTRACT: phase=expand（新增列，无破坏——同 0147/0148 惯例）
"""批 126：security_finding 加 description 列（漏洞一句话描述）。

用户裁定（2026-10-16）：「600 的描述太长了，一句话就可以，建议一次性让 AI 生成」。
设计：
- 采集侧存 grype 原始 description **截 120 字**（一句线——表格 show-overflow 一行
  恰好可读全；原 600 字是为 AI prompt 服务的，表格展示 120 够用且不撑库）；
- AI 一次性生成：**入库时**若 grype 无描述（GHSA 有时缺），用既有 llm_gateway
  生成一句话中文摘要（fail-soft 空）——"一次性"=写入时算好存列，非每次读时生成
  （读时生成=每请求一次 LLM 调用，浪费且不稳定）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0149"
down_revision: Union[str, Sequence[str], None] = "0148"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE security_finding ADD COLUMN IF NOT EXISTS description text")


def downgrade() -> None:
    op.execute("ALTER TABLE security_finding DROP COLUMN IF EXISTS description")
