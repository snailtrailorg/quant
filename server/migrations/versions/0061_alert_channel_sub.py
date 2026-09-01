"""批 7 · 告警订阅分发（2026-09-02，docs/任务/批7-告警订阅分发.md）

①alert_channel_sub 表：全局三行（im/email/sms），每行 target+categories(jsonb)+min_level+enabled
②notifications 加 dispatch jsonb 列：投递结局审计（ok/queued/failed:reason/skip:reason/null=未跑完；
  用户终裁"全程可审计"——消灭成功与死亡窗不可区分的静默态。小表 30 天清删，catalog-only 秒级，
  同 0059 ADD COLUMN 先例）
③permission seed：新权限键 alerts_config 仅 api 维一行（盲审 A2-P3/B2-P3：nav 维 resource
  必 ∈ NAV_ITEMS，不 seed；admin 专属=告警路由/计费短信面不给 analyst——其现含 system_config）
④system_config seed：alert_sms_* 四空键（盲审 B2-P4：凭证零写路径则"到位即通"不成立；
  照 0031 smtp 先例——secret 型加密列，专用端点写，动态读零重启）

Revision ID: 0061
Revises: 0060
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0061"
down_revision: str = "0060"
branch_labels: Union[Sequence[str], str, None] = None
depends_on: Union[Sequence[str], str, None] = None

_SMS_KEYS = [
    ("alert_sms_access_key_id", "", "text", "阿里云短信 AccessKey ID"),
    ("alert_sms_access_key_secret", "", "password", "阿里云短信 AccessKey Secret（加密，不回显）"),
    ("alert_sms_sign_name", "", "text", "阿里云短信签名（如 SnailQuant）"),
    ("alert_sms_template_code", "", "text", "阿里云短信模板 CODE（模板须含 level/title 两变量）"),
]


def upgrade() -> None:
    # ① 订阅表（IF NOT EXISTS+BIGSERIAL，0056 风格）
    op.execute("""
    CREATE TABLE IF NOT EXISTS alert_channel_sub (
        id          BIGSERIAL PRIMARY KEY,
        channel     TEXT NOT NULL CHECK (channel IN ('im','email','sms')),
        target      TEXT,
        categories  JSONB NOT NULL DEFAULT '[]'::jsonb,
        min_level   TEXT NOT NULL DEFAULT 'warn' CHECK (min_level IN ('warn','critical')),
        enabled     BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_alert_channel UNIQUE (channel)
    )""")
    # ② 投递结局审计列（0059 ADD COLUMN 同款）
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS dispatch JSONB")
    # ③ 权限键 seed：仅 api 维（不碰 nav 维——resource 必 ∈ NAV_ITEMS）
    op.execute(
        "INSERT INTO permission (subject_type, subject_id, dimension, resource, effect, note) "
        "VALUES ('role', 'admin', 'api', 'alerts_config', 'allow', '批7 seed：告警订阅/SMS 凭证管理') "
        "ON CONFLICT DO NOTHING")
    # ④ SMS 凭证空键 seed（0031 smtp 先例）
    for key, val, vtype, desc in _SMS_KEYS:
        op.execute(
            "INSERT INTO system_config (key, value, value_type, description) "
            f"VALUES ('{key}', '{val}', '{vtype}', '{desc}') "
            "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    # 与 upgrade 对称（盲审 B2-15/A2-P3：perm 行级删，不 drop 整表——permission 是共享表）
    op.execute("DELETE FROM permission WHERE resource = 'alerts_config' AND dimension = 'api'")
    for key, _, _, _ in _SMS_KEYS:
        op.execute(f"DELETE FROM system_config WHERE key='{key}'")
    op.execute("DROP TABLE IF EXISTS alert_channel_sub")
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS dispatch")
