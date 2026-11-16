"""批185（expand）：`security_finding` 增 `scanner` 列并回填（为 source 改名做两步走第一步）。

`security_component.source` 是**域**（pip/os/binary），finding 的 `source` 是**扫描器**
（grype/pip-audit）——同名不同义。改名属破坏性 op（migration_policy: bare_rename_column），
按立法拆两步：
- 本迁移（expand）：加 `scanner` 列 + 回填；代码**双写**两列，读侧切 scanner。
- 下一发布（contract，pair=0190）：`DROP COLUMN source`。

Revision ID: 0190
Revises: 0189
"""
from alembic import op

revision = "0190"
down_revision = "0189"


def upgrade() -> None:
    op.execute("ALTER TABLE security_finding ADD COLUMN IF NOT EXISTS scanner text")
    op.execute("UPDATE security_finding SET scanner = source WHERE scanner IS NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE security_finding DROP COLUMN IF EXISTS scanner")
