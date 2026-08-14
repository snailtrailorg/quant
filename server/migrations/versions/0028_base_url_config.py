"""base_url 系统配置项（邮件链接 base，Web 可改；留空=自动取访问 hostname）

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-13

把邮件链接 base（邀请/重置密码）做成 system_config 配置项，管理员可在 Web「系统配置」页改。
读取优先级（email_service._resolve_base_url）：
  1. system_config.base_url 非空 → 用它（管理员显式覆盖）
  2. 请求 hostname（request.base_url）→ 缺省自适应（从哪个域名访问就用哪个）
  3. .env BASE_URL → 兼容开发期
种子留空（''），默认走 hostname，不硬编码域名。
"""
from alembic import op


revision: str = "0028"
down_revision: str = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO system_config (key, value, value_type, description) "
        "VALUES ('base_url', '', 'text', "
        "'邮件链接 base（邀请/重置密码）；留空=自动取访问域名，填 https://your.domain 覆盖；改后即时生效') "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM system_config WHERE key='base_url'")
