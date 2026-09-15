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
    # 批28 盲审 B P0 修正：alembic 1.18 Operations.execute 签名 (sqltext, *, execution_options)
    # 不收参数元组（原参数化写法 upgrade --sql 实测 TypeError）——0076 先例 f-string 常量拼接
    for key, value, desc in (
        ("user_bot_quota", "5", "用户 IM 通道上限"),
        ("platform_bot_quota", "10", "全平台 IM 通道上限"),
    ):
        op.execute(
            f"INSERT INTO system_config (key, value, description) "
            f"VALUES ('{key}', '{value}', '{desc}') ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    for key, _ in _SEEDS:
        op.execute(f"DELETE FROM system_config WHERE key = '{key}'")
