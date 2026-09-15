"""批27-29：bot 配额两键入 system_config（Web 可改——表行存在 RunConfig 才渲染编辑项，0076 先例）

Revision ID: 0078
Revises: 0077
Create Date: 2026-09-15
"""
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None

_SEEDS = [
    ("user_bot_quota", "5"),        # 每用户 IM 通道上限（原 im_bots.py 硬编码三副本）
    ("platform_bot_quota", "10"),   # 全平台活 bot 上限（1.8G 机器每 bot 子进程 ≈62MB——批B #0d 量级依据）
]


def upgrade() -> None:
    for key, value in _SEEDS:
        op.execute(
            "INSERT INTO system_config (key, value, description) VALUES (%s, %s, %s) "
            "ON CONFLICT (key) DO NOTHING",
            (key, value,
             "用户 IM 通道上限" if key == "user_bot_quota" else "全平台 IM 通道上限"))


def downgrade() -> None:
    for key, _ in _SEEDS:
        op.execute("DELETE FROM system_config WHERE key = %s", (key,))
