"""批33b：perm_resource 覆盖层表（注册表 DB 面——只改显示四字段）。

代码底座（perm_registry.py 字面量层）⊕ 本表覆盖（group_key/sort_order/label_json/
enabled）——条目集恒代码单源（阶段二红线：写端点拒注册表外 id，不可增删条目）。
零行=纯底座（上线零数据迁移）。回滚后残留行无害（旧代码不读本表）。

Downgrade 对称 DROP。
Revision ID: 0084
Revises: 0083
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0084"
down_revision = "0083"


def upgrade() -> None:
    op.create_table(
        "perm_resource",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("res_id", sa.Text(), nullable=False),
        sa.Column("group_key", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("label_json", postgresql.JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("kind", "res_id", name="pk_perm_resource"),
        sa.CheckConstraint("kind IN ('api','nav','market_op')", name="ck_perm_resource_kind"),
    )


def downgrade() -> None:
    op.drop_table("perm_resource")
