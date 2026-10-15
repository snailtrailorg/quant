# EXPAND-CONTRACT: phase=expand（新增列，无既有消费者破坏——同 0146 惯例）
"""批 124：组件台账加 origin 列——组件来源自解释。

用户需求（2026-10-15）：「加一列说明这个包/组件是怎么来的，这样我就不用问你
ffmpeg 是在哪安装的」——目的=信息自解释，不必外部询问。

origin 值由采集侧**自动推导**（不从手工备注——会烂）：
- python 包 → 'pip 安装'
- rpm 包 → '系统 rpm（dnf）'
- binary（site-packages 内原生库）→ '<宿主包> 捆绑'（如 PySide6/Qt/lib/libavcodec.so
  → 'PySide6 捆绑'；宿主=路径首段）
- 点名系统组件（system 域）→ '系统组件'
推导规则在 security_collector._component_origin（单一真源，DB 只存结果）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0147"
down_revision: Union[str, Sequence[str], None] = "0146"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE security_component ADD COLUMN IF NOT EXISTS origin text")


def downgrade() -> None:
    op.execute("ALTER TABLE security_component DROP COLUMN IF EXISTS origin")
