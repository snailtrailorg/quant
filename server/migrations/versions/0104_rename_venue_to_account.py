"""术语正名：venue → account（账号语义）+ exchange（交易所语义仅注释归位，无 DDL）。

D1-D6 的 venue 概念 = 交易账号（external_interface 行），行业主流叫 account
（vnpy AccountData / QuantConnect account / FIX Tag 1 Account）。本迁移把活跃 schema
里的 venue 对象全部 rename 为 account：

- 6 表 venue_id 列 → account_id（live_task/position_snapshot/position_refresh/
  account_snapshot/order_log/trade_log）
- venue_permission 表 → account_permission（含 venue_id 列）
- 7 个 FK 约束 + 1 个 PK 约束 + 10 个 NOT NULL 约束 rename（PG18 NOT NULL 是
  pg_constraint 行，列/表 rename 后约束名不自动跟随，须显式 rename 才彻底）
- 存量数据：reconcile_issue.issue_type 'cross_venue_reverse' → 'cross_account_reverse'

历史迁移 0097-0103 不改（alembic checksum 不可变）；交易所语义的 venue
（MARKET_OP_DECOMP 第三元 = BINANCE/OKX）在注释/docstring 归位 exchange，无 DDL。

Revision ID: 0104
Revises: 0103
"""
from alembic import op

revision = "0104"
down_revision = "0103"

# 6 表（有 venue_id 列）
_ACCOUNT_ID_TABLES = (
    "live_task", "position_snapshot", "position_refresh",
    "account_snapshot", "order_log", "trade_log",
)

# 6 表 FK 约束：(表, 旧名, 新名)
_FK_RENAMES = (
    ("live_task", "fk_live_task_venue", "fk_live_task_account"),
    ("position_snapshot", "fk_position_snapshot_venue", "fk_position_snapshot_account"),
    ("position_refresh", "fk_position_refresh_venue", "fk_position_refresh_account"),
    ("account_snapshot", "fk_account_snapshot_venue", "fk_account_snapshot_account"),
    ("order_log", "fk_order_log_venue", "fk_order_log_account"),
    ("trade_log", "fk_trade_log_venue", "fk_trade_log_account"),
)

# 4 表有 venue_id 列的 NOT NULL 约束（order_log/trade_log 可空，无此约束）
_NOTNULL_ID_TABLES = ("live_task", "position_snapshot", "position_refresh", "account_snapshot")

# venue_permission 的 NOT NULL 列（除 venue_id 外的列名不变，仅表名前缀改）
_PERM_NOTNULL_COLS = (
    "allowed_categories", "allowed_exchanges", "allowed_boards",
    "is_st_allowed", "convertible_allowed",
)


def upgrade() -> None:
    # 1. 6 表 venue_id 列 → account_id
    for tbl in _ACCOUNT_ID_TABLES:
        op.execute(f"ALTER TABLE {tbl} RENAME COLUMN venue_id TO account_id")

    # 2. 6 表 FK 约束 rename
    for tbl, old, new in _FK_RENAMES:
        op.execute(f"ALTER TABLE {tbl} RENAME CONSTRAINT {old} TO {new}")

    # 3. 4 表 venue_id 列的 NOT NULL 约束 rename
    for tbl in _NOTNULL_ID_TABLES:
        op.execute(f"ALTER TABLE {tbl} RENAME CONSTRAINT "
                   f"{tbl}_venue_id_not_null TO {tbl}_account_id_not_null")

    # 4. venue_permission → account_permission（列 → 表 → 约束）
    op.execute("ALTER TABLE venue_permission RENAME COLUMN venue_id TO account_id")
    op.execute("ALTER TABLE venue_permission RENAME TO account_permission")
    # FK + PK
    op.execute("ALTER TABLE account_permission RENAME CONSTRAINT "
               "venue_permission_venue_id_fkey TO account_permission_account_id_fkey")
    op.execute("ALTER TABLE account_permission RENAME CONSTRAINT "
               "venue_permission_pkey TO account_permission_pkey")
    # venue_id 列的 NOT NULL（列名也变了）
    op.execute("ALTER TABLE account_permission RENAME CONSTRAINT "
               "venue_permission_venue_id_not_null TO account_permission_account_id_not_null")
    # 其余 5 列 NOT NULL（仅表名前缀变）
    for col in _PERM_NOTNULL_COLS:
        op.execute(f"ALTER TABLE account_permission RENAME CONSTRAINT "
                   f"venue_permission_{col}_not_null TO account_permission_{col}_not_null")

    # 5. 存量数据：对账类型
    op.execute("UPDATE reconcile_issue SET issue_type = 'cross_account_reverse' "
               "WHERE issue_type = 'cross_venue_reverse'")


def downgrade() -> None:
    # 数据回写
    op.execute("UPDATE reconcile_issue SET issue_type = 'cross_venue_reverse' "
               "WHERE issue_type = 'cross_account_reverse'")

    # account_permission 约束反向（NOT NULL → PK → FK）
    for col in reversed(_PERM_NOTNULL_COLS):
        op.execute(f"ALTER TABLE account_permission RENAME CONSTRAINT "
                   f"account_permission_{col}_not_null TO venue_permission_{col}_not_null")
    op.execute("ALTER TABLE account_permission RENAME CONSTRAINT "
               "account_permission_account_id_not_null TO venue_permission_venue_id_not_null")
    op.execute("ALTER TABLE account_permission RENAME CONSTRAINT "
               "account_permission_pkey TO venue_permission_pkey")
    op.execute("ALTER TABLE account_permission RENAME CONSTRAINT "
               "account_permission_account_id_fkey TO venue_permission_venue_id_fkey")
    # 表 → 列 反向
    op.execute("ALTER TABLE account_permission RENAME TO venue_permission")
    op.execute("ALTER TABLE venue_permission RENAME COLUMN account_id TO venue_id")

    # 4 表 NOT NULL 反向
    for tbl in reversed(_NOTNULL_ID_TABLES):
        op.execute(f"ALTER TABLE {tbl} RENAME CONSTRAINT "
                   f"{tbl}_account_id_not_null TO {tbl}_venue_id_not_null")

    # 6 表 FK 反向
    for tbl, old, new in reversed(_FK_RENAMES):
        op.execute(f"ALTER TABLE {tbl} RENAME CONSTRAINT {new} TO {old}")

    # 6 表列反向
    for tbl in reversed(_ACCOUNT_ID_TABLES):
        op.execute(f"ALTER TABLE {tbl} RENAME COLUMN account_id TO venue_id")
