"""screen_preset：标的筛选方案表（批 164b 期 2/3）。

契约（任务文件 v3 步骤 1+双盲处置）：
- 双部分唯一索引：builtin 全局 (category,name)；用户域 (category,created_by,name)
- created_by FK ON DELETE CASCADE；definition 浅 CHECK jsonb object
- permission 表 api 维 screen_preset 行（批 77 先例——A-P0 三件套之一）
- builtin 种子 3 条（红利蓝筹/动量强势/双低前百）

Revision ID: 0166
Revises: 0165
"""
from alembic import op
import sqlalchemy as sa

revision = "0166"
down_revision = "0165"

BUILTIN_SEEDS = [
    ("红利蓝筹", "stock", "builtin",
     '{"v":1,"match":"all","rows":[{"field":"dv_ratio","op":">=","value":3},{"field":"pe_ttm","op":"<","value":30},{"field":"st_filter","op":"=","value":true}]}'),
    ("动量强势", "stock", "builtin",
     '{"v":1,"match":"all","rows":[{"field":"momentum_20","op":">=","value":0.05},{"field":"price_vs_ma250","op":">=","value":0}]}'),
    ("双低前百", "convertible", "builtin",
     '{"v":1,"match":"all","rows":[{"field":"double_low","op":"<","value":999}]}'),
]


def upgrade():
    op.create_table(
        "screen_preset",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("category IN ('stock','listed_fund','convertible')", name="ck_preset_category"),
        sa.CheckConstraint("source IN ('builder','dsl','builtin')", name="ck_preset_source"),
        sa.CheckConstraint("jsonb_typeof(definition::jsonb) = 'object'", name="ck_preset_def_obj"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("uq_preset_builtin", "screen_preset", ["category", "name"],
                    unique=True, postgresql_where=sa.text("source='builtin'"))
    op.create_index("uq_preset_user", "screen_preset", ["category", "created_by", "name"],
                    unique=True, postgresql_where=sa.text("source!='builtin'"))
    # 权限行（批 77 先例：api 维；列名对齐真 schema——subject_id/dimension/resource）
    op.execute("INSERT INTO permission (subject_type, subject_id, dimension, resource, effect) "
               "SELECT 'role', 'admin', 'api', 'screen_preset', 'allow' "
               "WHERE NOT EXISTS (SELECT 1 FROM permission WHERE subject_type='role' AND subject_id='admin' "
               "AND dimension='api' AND resource='screen_preset')")
    for name, cat, src, definition in BUILTIN_SEEDS:
        op.execute(
            sa.text("INSERT INTO screen_preset (name, category, source, definition) "
                    "SELECT :n, :c, :s, CAST(:d AS jsonb) "
                    "WHERE NOT EXISTS (SELECT 1 FROM screen_preset "
                    "WHERE source='builtin' AND name=:n)")
            .bindparams(n=name, c=cat, s=src, d=definition))


def downgrade():
    op.execute("DELETE FROM permission WHERE resource_type='api' AND resource_key='screen_preset'")
    op.drop_index("uq_preset_user", table_name="screen_preset")
    op.drop_index("uq_preset_builtin", table_name="screen_preset")
    op.drop_table("screen_preset")
