"""IM 归属用户（批11C：owner_user_id + 绑定语义翻新）。

- im_bot_config + owner_user_id INT NULL（NULL=平台级 bot，admin 面创建；非空=自助归属）
- im_bot_users + user_id INT NULL（语义翻新：im_user_id→平台账号绑定；既有行 user_id=NULL
  =首见留痕待绑定，不再经 role 列获权限——role 列回落已从身份链删除，双轨验证期后清列）
- seed：存量 feishu bot 全量归 admin（role='admin' LIMIT 1——生产 bot10 单个，staging 同法）

Revision ID: 0072
Revises: 0071
Create Date: 2026-09-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0072"
down_revision: str = "0071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("im_bot_config",
                  sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.add_column("im_bot_users",
                  sa.Column("user_id", sa.Integer(), nullable=True))
    # 代码盲审 A-P0-1/B-P0-2：存量 feishu bot **保留 owner NULL=平台级**（原 v2"全量归 admin"自相矛盾：
    # 归 admin 后①卡片平台级门（owner IS NULL）无 bot→卡片面全灭 ②存量用户绑定死路+admin 一键绑定
    # 陌生人=提权陷阱）。存量 bot 留平台级，绑定恢复走管理面 upsert 的 user_id 参数。
    op.create_index("ix_im_bot_users_user", "im_bot_users", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_im_bot_users_user", table_name="im_bot_users")
    op.drop_column("im_bot_users", "user_id")
    op.drop_column("im_bot_config", "owner_user_id")
