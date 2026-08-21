"""IM 统一接入批 1:im_bot_config/im_bot_users(19 号 v2,2026-08-21)

feishu_config 全列数据迁移到统一表(不 DROP——批 2 切完读路径后 0052 再删)。
漂移实证顺带修正:feishu_config 早有 verification_token/encrypt_key 加密列且扫码
流程写入过,但签名代码读 env 从未读库列——本批签名改读新表(env 兜底过渡)。

Revision ID: 0051
Revises: 0050
Create Date: 2026-08-21
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0051"
down_revision: str = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROLES = "('viewer','analyst','trader','admin')"


def upgrade() -> None:
    op.create_table("im_bot_config",
        sa.Column("id", sa.Integer(), autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("default_role", sa.Text(), nullable=False, server_default="viewer"),
        sa.Column("lang", sa.Text(), server_default="zh"),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0")),
        sa.Column("credentials_encrypted", sa.Text()),   # 加密 JSON(仅 secret 字段)
        sa.Column("params", sa.dialects.postgresql.JSONB(), server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(f"default_role IN {_ROLES}", name="ck_imbot_default_role"),
    )
    # 表达式唯一约束(防 route_key 双存储漂移)——alembic 表达式约束不支持内联,走 DDL 索引
    op.execute("CREATE UNIQUE INDEX uq_imbot_provider_route ON im_bot_config (provider, (params->>'route_key'))")
    op.create_table("im_bot_users",
        sa.Column("id", sa.Integer(), autoincrement=True),
        sa.Column("bot_id", sa.Integer(), nullable=False),
        sa.Column("im_user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["bot_id"], ["im_bot_config.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("bot_id", "im_user_id"),
        sa.CheckConstraint(f"role IN {_ROLES}", name="ck_imbot_users_role"),
    )

    # ── feishu_config 全列数据迁移 ──
    from src.quant_common.crypto import decrypt, encrypt
    import json
    import logging as _logging
    _log = _logging.getLogger("alembic.migration")
    _failed = {"n": 0}

    def _dec(v: str | None) -> str:
        """容错解密:解不开置空(env 兜底过渡)。失败计数,迁移收尾 loud warning
        (双盲 A-S2:静默重加密垃圾+零告警会让批 2 切表时 bot 静默死亡无人知)。"""
        if not v:
            return ""
        try:
            return decrypt(v)
        except Exception:
            _failed["n"] += 1
            return ""

    conn = op.get_bind()
    # 双盲 A-S1:扫码流程可 INSERT 重复 app_id 行(旧行不停用)——DISTINCT ON 取最新,
    # 防撞 uq_imbot_provider_route 使整个迁移事务回滚
    rows = conn.execute(sa.text(
        "SELECT DISTINCT ON (app_id) id, app_id, app_secret_encrypted, "
        "verification_token_encrypted, encrypt_key_encrypted, enabled, name, role, description "
        "FROM feishu_config ORDER BY app_id, id DESC")).fetchall()
    for (fid, app_id, secret_enc, tok_enc, ek_enc, enabled, name, role, desc) in rows:
        creds = {"app_id": app_id or "", "app_secret": _dec(secret_enc)}
        tok, ek = _dec(tok_enc), _dec(ek_enc)
        if tok:
            creds["verification_token"] = tok
        if ek:
            creds["encrypt_key"] = ek
        # 双盲 A-G1:旧表 role 无 CHECK 且 web 更新端点曾裸收字符串——归一四角色,防撞 CHECK
        role_norm = role if role in ("viewer", "analyst", "trader", "admin") else "viewer"
        # 双盲 A-S2:creds 全空(解密全失败)存 NULL 而非重加密空值垃圾——批 2 切表前可见
        creds_enc = encrypt(json.dumps(creds, ensure_ascii=False)) if any(
            creds.get(k) for k in ("app_secret", "verification_token", "encrypt_key")) else None
        conn.execute(sa.text(
            "INSERT INTO im_bot_config (id, provider, name, description, default_role, "
            "enabled, priority, credentials_encrypted, params) "
            "VALUES (:id, 'feishu', :name, :desc, :role, :enabled, 0, :creds, :params)"),
            {"id": fid, "name": name, "desc": desc, "role": role_norm,
             "enabled": bool(enabled), "creds": creds_enc,
             "params": json.dumps({"route_key": app_id or ""})})
    # 序列拨正(显式 id 插入后)
    if rows:
        conn.execute(sa.text("SELECT setval('im_bot_config_id_seq', (SELECT MAX(id) FROM im_bot_config))"))
    if _failed["n"]:
        _log.warning("[0051] %d 个旧密文解密失败已置空(密钥轮换/历史密钥?)——credentials 存 NULL,"
                     "签名/授权走 env 兜底;批 2 切 FeishuClient 前须用当轮密钥在 Web 重录凭证!", _failed["n"])
    # event_types 死列明示丢弃(19 号 v2 §2)


def downgrade() -> None:
    # 数据已并回 feishu_config(批 2 前它仍在)——直接删新表
    op.drop_table("im_bot_users")
    op.drop_table("im_bot_config")
