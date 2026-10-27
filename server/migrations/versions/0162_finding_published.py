# 批 154：安全页双改造——漏洞公开时间列 + 依赖滞后维度
# 0162：security_finding.published_date（CVE 公开日——grype vulnerability.published）

revision = "0162"
down_revision = "0161"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("security_finding", sa.Column("published_date", sa.Date(), nullable=True))
    # 回填：无历史真值可回溯（旧数据只有我们的 first_seen），保持 NULL——前端回退 first_seen


def downgrade():
    op.drop_column("security_finding", "published_date")


from alembic import op
import sqlalchemy as sa
