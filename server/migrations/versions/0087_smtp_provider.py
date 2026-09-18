"""批47：SMTP 多实例容灾——smtp_provider 表（批43 纯拖拽单层同模式）+email_outbox failover 两列。

用户裁定（2026-09-18）：行序即 failover 顺序；单通道共尝试 smtp_max_attempts 次（默认 3，
**总尝试=配额**——用户裁定 A）后换下一条；轮转一圈全失败=终态 failed；新邮件恒从第一条开始。
存量迁移幂等：smtp_username 非空才建行（原 _smtp_config 判定同口径）；密码**搬密文不重加密**；
upgrade 删旧六键防双源（批43 孤儿键先例）。

Downgrade=best-effort 回填：首行（position ASC）平移回六键+drop 表/两列
（升级后新增行丢弃——回滚窗短可接受，同批43 声明）。
Revision ID: 0087
Revises: 0086
"""
from alembic import op
import sqlalchemy as sa

revision = "0087"
down_revision = "0086"

# 旧六键（smtp_max_attempts 是新键不在列）
_OLD_KEYS = ("smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_password", "smtp_from")


def upgrade() -> None:
    op.create_table(
        "smtp_provider",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False, server_default=""),
        sa.Column("port", sa.Integer(), nullable=False, server_default=sa.text("587")),
        sa.Column("security", sa.Text(), nullable=False, server_default="auto"),
        sa.Column("username", sa.Text(), nullable=False, server_default=""),
        sa.Column("password", sa.Text(), nullable=False, server_default=""),   # Fernet 密文（存量搬密文不重加密）
        sa.Column("from_addr", sa.Text(), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("security IN ('auto', 'ssl', 'starttls')", name="ck_smtp_provider_sec"),
        sa.CheckConstraint("position >= 0", name="ck_smtp_provider_pos"),
    )
    # 存量迁移：核心键=smtp_username 非空才建行（原 _smtp_config 判定同口径——批43 P0-1 教训：
    # 核心键进 FROM（缺=零行不建），可选键 COALESCE 标量子查询（缺=回落缺省，不丢已建行）
    op.execute(r"""
        INSERT INTO smtp_provider (name, host, port, security, username, password, from_addr,
                                   position, enabled)
        SELECT '默认',
               COALESCE((SELECT value FROM system_config WHERE key='smtp_host'), ''),
               COALESCE(NULLIF(TRIM(COALESCE((SELECT value FROM system_config WHERE key='smtp_port'), '')), ''), '587')::int,
               COALESCE((SELECT value FROM system_config WHERE key='smtp_security'), 'auto'),
               u.v,
               COALESCE((SELECT value FROM system_config WHERE key='smtp_password'), ''),
               COALESCE(NULLIF(TRIM(COALESCE((SELECT value FROM system_config WHERE key='smtp_from'), '')), ''), u.v),
               0, true
        FROM (SELECT value v FROM system_config WHERE key='smtp_username') u
        WHERE NOT EXISTS (SELECT 1 FROM smtp_provider)
          AND COALESCE(trim(u.v), '') <> ''""")
    # email_outbox failover 两列（批47：provider_id null=下次用第一实例；provider_attempts=当前实例已试次数）
    op.add_column("email_outbox", sa.Column("provider_id", sa.BigInteger(), nullable=True))
    op.add_column("email_outbox", sa.Column("provider_attempts", sa.Integer(), nullable=False, server_default="0"))
    # 配置键 + 删旧六键防双源（'smtp\_%' 转义下划线——精确前缀；smtp_max_attempts 是新键不在删除集）
    op.execute("INSERT INTO system_config (key, value, value_type, description) VALUES "
               "('smtp_max_attempts', '3', 'int', '每条邮件通道最多尝试次数，达到后切换下一条') "
               "ON CONFLICT (key) DO NOTHING")
    op.execute("DELETE FROM system_config WHERE key LIKE 'smtp\\_%' AND key <> 'smtp_max_attempts'")


def downgrade() -> None:
    # best-effort 回填：首行（position ASC）平移回六键（密码密文平移；port Integer 需 ::text——
    # COALESCE(int,'') 混型必炸 InvalidTextRepresentation，dev 双向重放实证）；升级后新增行丢弃（同批43 声明）
    _cols = ("host", "COALESCE(port::text, '587')", "security", "username", "password", "from_addr")
    for k, col in zip(_OLD_KEYS, _cols):
        op.execute(f"""
            INSERT INTO system_config (key, value, value_type, description)
            SELECT '{k}', COALESCE({col}, ''), 'text', ''
            FROM smtp_provider ORDER BY position ASC LIMIT 1
            ON CONFLICT (key) DO NOTHING""")
    op.execute("DELETE FROM system_config WHERE key = 'smtp_max_attempts'")
    op.drop_column("email_outbox", "provider_id")
    op.drop_column("email_outbox", "provider_attempts")
    op.drop_table("smtp_provider")
