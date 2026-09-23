"""多账号源 D4 段1：position_snapshot.direction 资源 id 化。

adapters.py 原用 vnpy Direction.value（gettext 翻译文本，随 locale 飘：中文「多/空/净」
或英文「Long/Short/Net」）落库，读方按 'short' 过滤恒真失效。D4 起落库统一资源 id
direction_long/direction_short/direction_net（.name.lower() + direction_ 前缀，显示层
多语言另做）。本迁移归一化存量多语言值。

Revision ID: 0103
Revises: 0102
"""
from alembic import op

revision = "0103"
down_revision = "0102"


def upgrade() -> None:
    # 中文 gettext 值
    op.execute("UPDATE position_snapshot SET direction='direction_long' WHERE direction='多'")
    op.execute("UPDATE position_snapshot SET direction='direction_short' WHERE direction='空'")
    op.execute("UPDATE position_snapshot SET direction='direction_net' WHERE direction='净'")
    # 英文大小写 gettext 值（Long/Short/Net、long/short/net、LONG/SHORT/NET）
    op.execute("UPDATE position_snapshot SET direction='direction_' || lower(direction) "
               "WHERE direction IN ('Long','Short','Net','long','short','net','LONG','SHORT','NET')")
    # 列默认值资源 id 化（0043 建表 server_default='long' 遗留裸值——盲审 A-5 潜伏不一致）
    op.alter_column("position_snapshot", "direction", server_default="direction_long")


def downgrade() -> None:
    # 反向：剥离 direction_ 前缀（有损——中文值已不可还原，回退到英文小写）
    op.alter_column("position_snapshot", "direction", server_default="long")
    op.execute("UPDATE position_snapshot SET direction=substring(direction FROM 11) "
               "WHERE direction LIKE 'direction_%'")
