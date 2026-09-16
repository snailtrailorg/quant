"""批30：通知订阅用户化 + users.phone。

新表 alert_user_sub（订阅维度=用户；用户三通道=邮箱/手机/名下 bot 由 dispatch 展开裁量）。
存量迁移：旧 alert_channel_sub 的 im 行 → bot 归属人订阅行（DISTINCT ON owner 取最早行，
JOIN users 防孤儿 owner 触 FK violation——盲审 A-P2-3）；email/sms 行不迁（地址→用户不可靠
映射，GET /api/alerts/config 带 legacy 清单提示重建——盲审 B-P2-7 过渡语义）。
旧表保留不读写（非破坏优先——DROP 属破坏性 DDL 门拦截项，清理列下批）。

Downgrade 可逆（DROP 新表 + users DROP phone；旧表未动无需回填）。
Revision ID: 0080
Revises: 0079
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0080"
down_revision = "0079"


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.Text(), nullable=True))
    op.create_table(
        "alert_user_sub",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("categories", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("min_level", sa.Text(), nullable=False, server_default="warn"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", name="uq_alert_user_sub_user"),
        sa.CheckConstraint("min_level IN ('warn', 'critical')", name="ck_alert_user_sub_level"),
    )
    # 存量 im 订阅 → owner 用户行（ORDER BY 保 DISTINCT ON 确定性——盲审 A-P1-1）
    op.execute("""
        INSERT INTO alert_user_sub (user_id, categories, min_level, enabled, created_at, updated_at)
        SELECT DISTINCT ON (b.owner_user_id)
               b.owner_user_id, a.categories, a.min_level, a.enabled, now(), now()
        FROM alert_channel_sub a
        JOIN im_bot_config b ON b.id::text = a.target AND a.channel = 'im'
        JOIN users u ON u.id = b.owner_user_id
        WHERE a.channel = 'im'
        ORDER BY b.owner_user_id, a.id
        ON CONFLICT (user_id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("alert_user_sub")
    op.drop_column("users", "phone")
