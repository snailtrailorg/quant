"""web 长尾第一档：notifications.code + factor_def.type（双 ALTER）。

产出 2：notifications 加 code 列——通知结构化 body 机制（15号复审遗留，
前端 runbook 映射以 code 为键；存量行 NULL 兼容）。
产出 3：factor_def 加 type 列——POST /factors 扩 DSL 类型（13号#2；
缺省 'python' 兼容存量，'dsl' 时 code 列存表达式）。
两表均小表（notifications 30 天清删/factor_def 仅建因子时写），ADD COLUMN
NULL 为 catalog-only 秒级（盲审 B 实核）。

Revision ID: 0059
Revises: 0058
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0059"
down_revision: str = "0058"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("code", sa.String(64), nullable=True))
    op.add_column("factor_def", sa.Column("type", sa.String(16), nullable=False,
                                          server_default="python"))


def downgrade() -> None:
    op.drop_column("factor_def", "type")
    op.drop_column("notifications", "code")
