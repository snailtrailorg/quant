"""批50：LLM 模型页重构——position 接管 priority（拖拽行序=优先级）+预算链彻底退役（DROP llm_budget）。

用户裁定（2026-09-18）：①priority DROP（发布走 allow_contract 通道——批11A 先例）②预算告警彻底停
（beat 任务/CRUD 端点/check 链/UI 全退役；表 DROP，downgrade 重建空表——批39 channel_config 先例）。
存量物化：position=ROW_NUMBER() OVER (ORDER BY priority, id)-1（tiebreaker id——default 10 并列实存）。
三配置键（冷却/阈值/重试间隔）入 system_config——未配=新缺省**新真源**，config.yaml 降级兜底。
Revision ID: 0088
Revises: 0087
"""
from alembic import op
import sqlalchemy as sa

revision = "0088"
down_revision = "0087"


def upgrade() -> None:
    # ——— llm_model_config：position 接管 priority ———
    op.add_column("llm_model_config",
                  sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.execute("""
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY priority, id) - 1 AS pos
            FROM llm_model_config)
        UPDATE llm_model_config m SET position = r.pos FROM ranked r WHERE m.id = r.id""")
    op.drop_column("llm_model_config", "priority")   # 裁定 A：DROP（批47 删六键同精神——不留双源残骸）
    # ——— 预算链彻底退役（裁定 B）———
    op.drop_table("llm_budget")
    # ——— 冷却三配置键（新真源；未配=此处缺省）———
    for k, v, desc in [
        ("llm_cooldown_min", "30", "模型失败切换后冷却时长（分钟）——期间完全不试该模型"),
        ("llm_fail_threshold", "5", "连续失败次数上限——达到后切换下一行模型并冷却"),
        ("llm_retry_wait_s", "2", "同一模型两次尝试之间的等待秒数"),
    ]:
        op.execute("INSERT INTO system_config (key, value, value_type, description) "
                   f"VALUES ('{k}', '{v}', 'int', '{desc}') ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    # priority 重建回填（position+1 往返自洽——1 起与旧 default 10 无关，保序即可）
    op.add_column("llm_model_config",
                  sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("10")))
    op.execute("UPDATE llm_model_config SET priority = position + 1")
    op.drop_column("llm_model_config", "position")
    # llm_budget 重建空表（批39 channel_config 先例——配置数据不回填，回滚窗内可接受；
    # 形状对齐 0020 原表：id=int/monthly=numeric/无 created_at）
    op.create_table(
        "llm_budget",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text()),
        sa.Column("daily_token_limit", sa.Integer()),
        sa.Column("monthly_cost_limit", sa.Numeric()),
        sa.Column("alert_threshold_pct", sa.Integer(), server_default=sa.text("80")),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    for k in ("llm_cooldown_min", "llm_fail_threshold", "llm_retry_wait_s"):
        op.execute(f"DELETE FROM system_config WHERE key='{k}'")
