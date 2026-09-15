"""批28-7：日志保留两键入 system_config（Web 可改）+audit_log ts 索引（GC 批删路径）

log_retention_days=30（0=不清理）/ audit_retention_days=0（0=永久保留缺省——两键 0 语义统一
"不清理"，杜绝"0=保留 0 天"误读路径=全表清空）。seed 四列含 value_type='int'（0076 先例——
0078 漏列是坑先例：string 型会绕过 int 校验+RunConfig 渲染文本框）。
索引照 0077 idx_system_log_ts 式（audit_log 建表 0001 无 ts 索引，retention>0 时批删全表扫）。

Revision ID: 0079
Revises: 0078
Create Date: 2026-09-16
"""
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None

_SEEDS = [
    ("log_retention_days", "30", "运行日志保留天数（0=不清理）"),
    ("audit_retention_days", "0", "审计日志保留天数（0=永久保留）"),
]


def upgrade() -> None:
    # 批28 盲审 B P0 修正：alembic 1.18 execute 不收参数元组——f-string 常量拼接（0076 先例）
    for key, value, desc in _SEEDS:
        op.execute(
            f"INSERT INTO system_config (key, value, value_type, description) "
            f"VALUES ('{key}', '{value}', 'int', '{desc}') ON CONFLICT (key) DO NOTHING")
    op.create_index("idx_audit_log_ts", "audit_log", ["ts"])


def downgrade() -> None:
    op.drop_index("idx_audit_log_ts", table_name="audit_log")
    for key, _, _ in _SEEDS:
        op.execute(f"DELETE FROM system_config WHERE key = '{key}'")
