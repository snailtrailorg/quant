"""多账号源 D2 段2：venue_id 回填 + 约束（NOT NULL/FK/PK 改键/删 account_id）。

回填规则（pre-D2 单账户 → 全映射到「默认 venue」= min 交易域 id）：
- live_task/position_snapshot/position_refresh/account_snapshot → 默认 venue
- order_log/trade_log → 历史行 venue_id 置 NULL（对账 cutover 前按旧 strategy_id 语义）
- account_key 不回填（资金账号在加密凭证，SQL 迁不出，用户经 CRUD 填）

FK 语义：live_task= RESTRICT（delete_interface 守卫防删有任务的 venue）；快照类=CASCADE
（随 venue 删）；order/trade_log=SET NULL（历史 NULL 保留）。

Revision ID: 0100
Revises: 0099
"""
from alembic import op
import sqlalchemy as sa

revision = "0100"
down_revision = "0099"

# 默认 venue = 最小 id 的交易域行（pre-D2 单账户）
_DEFAULT_VENUE = "(SELECT min(id) FROM external_interface WHERE 'trading' = ANY(capabilities))"


def upgrade() -> None:
    # 1. 回填 venue_id（单账户 → 默认 venue）
    for tbl in ("live_task", "position_snapshot", "position_refresh", "account_snapshot"):
        op.execute(f"UPDATE {tbl} SET venue_id = {_DEFAULT_VENUE} WHERE venue_id IS NULL")
    # order_log/trade_log：历史行保持 NULL（对账 cutover 前旧语义）

    # 2. live_task.venue_id NOT NULL + FK RESTRICT（delete_interface 守卫）
    op.alter_column("live_task", "venue_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_foreign_key("fk_live_task_venue", "live_task", "external_interface",
                          ["venue_id"], ["id"], ondelete="RESTRICT")

    # 3. position_snapshot PK 改键（account_id→venue_id）+ 删 account_id
    op.execute("""
        DELETE FROM position_snapshot a
        USING position_snapshot b
        WHERE a.venue_id = b.venue_id AND a.symbol = b.symbol AND a.direction = b.direction
          AND a.ctid < b.ctid
    """)
    op.alter_column("position_snapshot", "venue_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_constraint("position_snapshot_pkey", "position_snapshot", type_="primary")
    op.create_primary_key("position_snapshot_pkey", "position_snapshot",
                          ["venue_id", "symbol", "direction"])
    op.create_foreign_key("fk_position_snapshot_venue", "position_snapshot", "external_interface",
                          ["venue_id"], ["id"], ondelete="CASCADE")
    op.drop_column("position_snapshot", "account_id")

    # 4. position_refresh PK 改键 + 删 account_id
    op.alter_column("position_refresh", "venue_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_constraint("position_refresh_pkey", "position_refresh", type_="primary")
    op.create_primary_key("position_refresh_pkey", "position_refresh", ["venue_id"])
    op.create_foreign_key("fk_position_refresh_venue", "position_refresh", "external_interface",
                          ["venue_id"], ["id"], ondelete="CASCADE")
    op.drop_column("position_refresh", "account_id")

    # 5. account_snapshot.venue_id NOT NULL + FK（多行化：每 venue 各自行）
    op.alter_column("account_snapshot", "venue_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_foreign_key("fk_account_snapshot_venue", "account_snapshot", "external_interface",
                          ["venue_id"], ["id"], ondelete="CASCADE")

    # 6. order_log/trade_log venue_id FK（历史 NULL，SET NULL 保留）
    op.create_foreign_key("fk_order_log_venue", "order_log", "external_interface",
                          ["venue_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_trade_log_venue", "trade_log", "external_interface",
                          ["venue_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    # 反序还原 0100 的约束（venue_id 列由 0099 的 downgrade 删，此处不删列）。
    # 有损：venue_id→account_id 无唯一映射，account_id 回填占位 'default'。
    op.drop_constraint("fk_trade_log_venue", "trade_log", type_="foreignkey")
    op.drop_constraint("fk_order_log_venue", "order_log", type_="foreignkey")
    op.drop_constraint("fk_account_snapshot_venue", "account_snapshot", type_="foreignkey")
    op.drop_constraint("fk_position_refresh_venue", "position_refresh", type_="foreignkey")
    op.drop_constraint("fk_position_snapshot_venue", "position_snapshot", type_="foreignkey")
    op.drop_constraint("fk_live_task_venue", "live_task", type_="foreignkey")

    # position_snapshot：还原旧 PK（drop 新 PK → 加 account_id → 建旧 PK）
    op.drop_constraint("position_snapshot_pkey", "position_snapshot", type_="primary")
    op.add_column("position_snapshot",
                  sa.Column("account_id", sa.Text(), nullable=False, server_default="default"))
    op.create_primary_key("position_snapshot_pkey", "position_snapshot",
                          ["account_id", "symbol", "direction"])

    # position_refresh 同理
    op.drop_constraint("position_refresh_pkey", "position_refresh", type_="primary")
    op.add_column("position_refresh",
                  sa.Column("account_id", sa.Text(), nullable=False, server_default="default"))
    op.create_primary_key("position_refresh_pkey", "position_refresh", ["account_id"])
