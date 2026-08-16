"""smtp_* 配置描述修正：弃 .env 回退（2026-08-14 DB 单一真相源）

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-14

0031 的描述文案含"留空用 .env"，随后决策改为 DB 唯一（.env 不再参与），
未配置=发送失败（重试→铃铛）。本迁移仅更新 description 文案，无数据变更。
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0032"
down_revision: str = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UPDATES = [
    ("smtp_host", "SMTP 服务器（如 smtpdm.aliyun.com / smtp.qq.com）"),
    ("smtp_port", "SMTP 端口（默认 587 STARTTLS）"),
    ("smtp_username", "SMTP 用户名（发信账号；未配置=发送失败，重试耗尽铃铛提醒）"),
    ("smtp_from", "发件人地址（须与服务商一致；留空=同用户名）"),
]


def upgrade() -> None:
    for key, desc in _UPDATES:
        op.execute(f"UPDATE system_config SET description='{desc}' WHERE key='{key}'")


def downgrade() -> None:
    pass  # 文案回滚无意义
