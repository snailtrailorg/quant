"""批43：短信多服务商容灾——sms_provider 表（纯拖拽单层 position 序）。

用户裁定：行序即 failover 顺序（无优先级列）。存量迁移幂等：四核心键齐才建默认行
（verify_tpl 有则带上）；任何残留 alert_sms_* 键一律 DELETE（双源+partial 孤儿消灭）。

Downgrade=best-effort 回填：首行（position ASC）密文平移回五键+drop 表
（升级后新增行丢弃——回滚窗短可接受，声明进方案）。
Revision ID: 0086
Revises: 0085
"""
from alembic import op
import sqlalchemy as sa

revision = "0086"
down_revision = "0085"

_CORE = ("alert_sms_access_key_id", "alert_sms_access_key_secret",
         "alert_sms_sign_name", "alert_sms_template_code")


def upgrade() -> None:
    op.create_table(
        "sms_provider",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default="aliyun"),
        sa.Column("access_key_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("access_key_secret", sa.Text(), nullable=False, server_default=""),
        sa.Column("sign_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("alert_template_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("verify_template_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("provider IN ('aliyun')", name="ck_sms_provider_kind"),
        sa.CheckConstraint("position >= 0", name="ck_sms_provider_pos"),
    )
    # 存量迁移：四核心键齐 → 默认行（幂等守卫）
    # 批43 三轮盲审 P0-1：verify 键改**标量子查询**（第三轮双员同抓——原 CROSS JOIN 形态下
    # verify 键无行（批30 前配的凭证）→笛卡尔积零行→不建行+随后 DELETE 五键=存量凭证静默丢失）
    op.execute("""
        INSERT INTO sms_provider (name, provider, access_key_id, access_key_secret,
                                  sign_name, alert_template_code, verify_template_code,
                                  position, enabled)
        SELECT '默认', 'aliyun', k.v1, s.v2, g.v3, t.v4,
               COALESCE((SELECT value FROM system_config
                         WHERE key='alert_sms_verify_template_code'), ''), 0, true
        FROM (SELECT value v1 FROM system_config WHERE key='alert_sms_access_key_id') k,
             (SELECT value v2 FROM system_config WHERE key='alert_sms_access_key_secret') s,
             (SELECT value v3 FROM system_config WHERE key='alert_sms_sign_name') g,
             (SELECT value v4 FROM system_config WHERE key='alert_sms_template_code') t
        WHERE NOT EXISTS (SELECT 1 FROM sms_provider)
          AND COALESCE(trim(k.v1),'')<>'' AND COALESCE(trim(s.v2),'')<>''
          AND COALESCE(trim(g.v3),'')<>'' AND COALESCE(trim(t.v4),'')<>''""")
    op.execute("DELETE FROM system_config WHERE key LIKE 'alert_sms_%'")


def downgrade() -> None:
    # best-effort 回填：首行（position ASC）密文平移回五键；升级后新增行丢弃（回滚窗短可接受）
    _backfill = [
        ("alert_sms_access_key_id", "access_key_id", "text", "短信 AK（批43 回填）"),
        ("alert_sms_access_key_secret", "access_key_secret", "password", "短信密钥（批43 回填）"),
        ("alert_sms_sign_name", "sign_name", "text", "短信签名（批43 回填）"),
        ("alert_sms_template_code", "alert_template_code", "text", "告警模板（批43 回填）"),
        ("alert_sms_verify_template_code", "verify_template_code", "text", "验证码模板（批43 回填）"),
    ]
    for key, col, vtype, desc in _backfill:
        op.execute(
            f"INSERT INTO system_config (key, value, value_type, description) "
            f"SELECT '{key}', {col}, '{vtype}', '{desc}' FROM sms_provider "
            f"ORDER BY position, id LIMIT 1 "
            f"ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value")
    op.drop_table("sms_provider")
