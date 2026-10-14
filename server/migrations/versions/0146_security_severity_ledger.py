# EXPAND-CONTRACT: phase=expand（新增性迁移——severity 列/台账表无既有消费者，contract 随表退役批次走，同 0145 惯例）
"""批 123：漏洞源重构线①——`security_finding` 加 severity 列 + `security_component` 组件台账。

任务＝`flow/任务/批123-漏洞源重构.md`（grype+syft 插件化/severity/台账/前端修正）。

**severity（finding 加列）**：单词表 Critical/High/Medium/Low/Negligible/Unknown——
grype CVSS 词表基准；dnf errata RH 词表（Important/Moderate…）**写入侧归一**
（collector 映射，词表在代码=0145「写入侧单源」立法延续，DB 不加域 CHECK）。
NULL=未评级（存量行与无评分漏洞诚实显示，不造假值）。

**security_component（组件台账，一等表）**：版本变更史入库（动态数据入库原则——
替代快照 jsonb 反查 30 天窗口）。UPSERT 语义：版本未变只推 last_seen；变了同步
version+last_changed；新组件三日同值入库。归源规则（防双计数）：rpm 库 python3-*
归 'rpm'；pip 装的归 'pip'（venv 运行时真身）；8 个点名系统组件归 'system'
（collector _collect_components 既有清单）。

**写法（仓例）**：全部 `op.execute(<内联字面量>)`（`--sql` 离线渲染安全，同 0132~0145）；
`CREATE TABLE IF NOT EXISTS` + `DROP ... IF EXISTS`（复跑/回滚安全）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0146"
down_revision: Union[str, Sequence[str], None] = "0145"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE security_finding ADD COLUMN IF NOT EXISTS severity text"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS security_component (
            id          bigserial PRIMARY KEY,
            source      text NOT NULL,
            name        text NOT NULL,
            version     text NOT NULL,
            first_seen  date NOT NULL,
            last_changed date NOT NULL,
            last_seen   date NOT NULL,
            CONSTRAINT uq_security_component UNIQUE (source, name)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_security_component_source "
        "ON security_component (source)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS security_component")
    op.execute("ALTER TABLE security_finding DROP COLUMN IF EXISTS severity")
