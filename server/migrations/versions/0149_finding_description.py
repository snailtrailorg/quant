# EXPAND-CONTRACT: phase=expand（新增列，无破坏——同 0147/0148 惯例）
"""批 126：security_finding 加 description/description_en 列（漏洞一句话描述·双语）。

用户裁定（2026-10-16）：「600 的描述太长了，一句话就可以，建议一次性让 AI 生成」+
「描述为空就提交 AI 生成中英文两种格式再更新——①存量可补 ②失败可补」（幂等收敛：
空=待办，非空=完成，失败留空下轮再补）。
设计：
- 采集侧存 grype 原始 description **截 600 字**（AI prompt 输入，不入库）；
- AI 一次性生成：**入库时**生成中英双语一句话（≤120 字）存两列——"一次性"=写入时
  算好存列，非每次读时生成（读时=每请求一次 LLM 调用，浪费且不稳定）；
- 幂等补齐通道（_backfill_descriptions）：全表 `WHERE description IS NULL`
  （含 upsert 永不触碰的已修复存量行）每轮 cap 20 行分轮收敛。
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
    op.execute(
        "ALTER TABLE security_finding ADD COLUMN IF NOT EXISTS description_en text")


def downgrade() -> None:
    op.execute("ALTER TABLE security_finding DROP COLUMN IF EXISTS description_en")
    op.execute("ALTER TABLE security_finding DROP COLUMN IF EXISTS description")
