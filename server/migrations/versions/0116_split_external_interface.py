"""批 83a：external_interface 拆表 —— data_source + trading_account（重构，行为不变）。

**抽象判据**（2026-09-29 用户裁定）：数据源账号（Tushare 拉取）与交易账号（XTP/EMT 下单+行情）
功能完全不同、无继承关系，用能力集 5 token 强行揉合成一类实体 = 过度统一（错误统一）。
拆两张表：列集各取所需（data_source 无 exchanges/account_key；trading_account 有）。

四要点（盲审 P0-1，一个都不能少）：
1. **两域行均保留原 id** 迁入新表 —— 7 表 account_id FK 零重映射 + data_source_usage.interface_id
   零迁移（该列无 FK 约束，仅取值引用；0090 建列时为裸 BigInteger）。
2. 7 表 FK 重指向 trading_account.id（约束名保持 0104 现行名，只换目标表）。
3. account_key UNIQUE(provider, account_key) 在 trading_account 上重建。
4. **显式 drop FK 再 drop 表**（禁裸 DROP 的静默 CASCADE 删 FK）；顺序=先建新表→搬迁→
   显式 drop 旧 FK→重指向→显式 drop 旧索引/表。破坏性 DDL → 部署走 allow_contract 通道。

数据加工点：
- **tencent 僵尸行退役**（盲审 P1-2）：0096 退役自攒分钟线后其唯一能力 rt_quote 无代码消费
  （分时/快照走 market_snapshot 的 HTTP 直取，不读接口行），行直接删。
- 序列复位：两表显式插入原 id 后须 setval，否则后续 INSERT 撞既有 id（新表各自新序列）。

存量分域判据 = 盲审 P0-1 原文：`'trading' = ANY(capabilities)` → trading_account，其余 → data_source
（与 0090 分域段、mgmt 域谓词同源；拆表后该谓词在表层面退役）。

downgrade：还原 external_interface（0090 结构 + 0099 的 account_key/唯一索引），两表 UNION ALL
按原 id 回填，7 表 FK 重指回 external_interface.id。**tencent 行不可恢复**（数据删除不可逆，
与 0096 先例同口径）。

Revision ID: 0116
Revises: 0115
"""
from alembic import op
import sqlalchemy as sa

revision = "0116"
down_revision = "0115"

# 现行 7 表 FK（0104 术语正名后的约束名）：(表, 约束名, 列, ON DELETE)
# ON DELETE 语义逐表对齐 0100（live_task RESTRICT 防删实盘任务账号；快照族 CASCADE；
# 订单/成交 SET NULL 保历史行；权限行 CASCADE）
_FKS = (
    ("live_task", "fk_live_task_account", "account_id", "RESTRICT"),
    ("position_snapshot", "fk_position_snapshot_account", "account_id", "CASCADE"),
    ("position_refresh", "fk_position_refresh_account", "account_id", "CASCADE"),
    ("account_snapshot", "fk_account_snapshot_account", "account_id", "CASCADE"),
    ("order_log", "fk_order_log_account", "account_id", "SET NULL"),
    ("trade_log", "fk_trade_log_account", "account_id", "SET NULL"),
    ("account_permission", "account_permission_account_id_fkey", "account_id", "CASCADE"),
)

_TRADING_PRED = "'trading' = ANY(capabilities)"
_DATA_PRED = f"NOT ({_TRADING_PRED})"

# 两表列（除 id）——搬迁/回填共用一份，防两侧列名漂移
_SHARED_COLS = ("name, provider, market, credentials_encrypted, params, "
                "capabilities, position, enabled, created_at, updated_at")

# 旧表索引（显式 drop，禁靠 drop_table 隐式连带）
_OLD_INDEXES = (
    "ix_external_interface_provider",
    "ix_external_interface_position",
    "ix_external_interface_capabilities",
    "ix_external_interface_account_key",
)


def upgrade() -> None:
    # 0. tencent 僵尸行退役（P1-2：唯一能力 rt_quote 无代码消费，功能走 HTTP 直取）
    op.execute("DELETE FROM external_interface WHERE provider = 'tencent'")

    # 1. 建两表（列集各取所需：data_source 无 exchanges/account_key）
    op.create_table(
        "data_source",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text()),
        sa.Column("params", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("capabilities", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_data_source_provider", "data_source", ["provider"])
    op.create_index("ix_data_source_position", "data_source", ["position"])
    op.create_index("ix_data_source_capabilities", "data_source", ["capabilities"],
                    postgresql_using="gin")

    op.create_table(
        "trading_account",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("exchanges", sa.ARRAY(sa.Text()), nullable=True),   # NULL=注册表该市场全所
        sa.Column("account_key", sa.Text(), nullable=True),           # D2 语义键（资金账号）
        sa.Column("credentials_encrypted", sa.Text()),
        sa.Column("params", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("capabilities", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_trading_account_provider", "trading_account", ["provider"])
    op.create_index("ix_trading_account_position", "trading_account", ["position"])
    op.create_index("ix_trading_account_capabilities", "trading_account", ["capabilities"],
                    postgresql_using="gin")
    # D2 语义键唯一（与 0099 同列对；NULL 不参与唯一判定——多行可都不填资金账号）
    op.create_index("ix_trading_account_account_key", "trading_account",
                    ["provider", "account_key"], unique=True)

    # 2. 搬迁（保留原 id —— FK 与用量表 interface_id 零重映射）
    op.execute(f"""
        INSERT INTO data_source (id, {_SHARED_COLS})
        SELECT id, {_SHARED_COLS} FROM external_interface WHERE {_DATA_PRED}
    """)
    op.execute(f"""
        INSERT INTO trading_account (id, {_SHARED_COLS}, exchanges, account_key)
        SELECT id, {_SHARED_COLS}, exchanges, account_key
        FROM external_interface WHERE {_TRADING_PRED}
    """)
    # 序列复位（显式 id 插入后，序列仍停在 1——不复位则后续 INSERT 撞主键）
    for tbl in ("data_source", "trading_account"):
        op.execute(f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), "
                   f"coalesce((SELECT max(id) FROM {tbl}), 1))")

    # 3. 显式 drop 7 表 FK（禁裸 drop_table 的 CASCADE 静默连带）
    for tbl, cname, _col, _act in _FKS:
        op.drop_constraint(cname, tbl, type_="foreignkey")

    # 4. FK 重指向 trading_account.id（约束名不变——消费方/回滚脚本无需改）
    for tbl, cname, col, act in _FKS:
        op.create_foreign_key(cname, tbl, "trading_account", [col], ["id"], ondelete=act)

    # 5. 显式 drop 旧索引 + 旧表（此时 FK 已全部摘除，裸 drop 亦不会静默连带）
    for idx in _OLD_INDEXES:
        op.drop_index(idx, table_name="external_interface")
    op.drop_table("external_interface")


def downgrade() -> None:
    # 1. 摘除指向 trading_account 的 7 表 FK
    for tbl, cname, _col, _act in _FKS:
        op.drop_constraint(cname, tbl, type_="foreignkey")

    # 2. 还原 external_interface（0090 结构 + 0099 的 account_key）
    op.create_table(
        "external_interface",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("exchanges", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("credentials_encrypted", sa.Text()),
        sa.Column("params", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("capabilities", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("account_key", sa.Text(), nullable=True),
    )
    op.create_index("ix_external_interface_provider", "external_interface", ["provider"])
    op.create_index("ix_external_interface_position", "external_interface", ["position"])
    op.create_index("ix_external_interface_capabilities", "external_interface",
                    ["capabilities"], postgresql_using="gin")
    op.create_index("ix_external_interface_account_key", "external_interface",
                    ["provider", "account_key"], unique=True)

    # 3. 两表按原 id 回填（id 两域互斥，UNION ALL 无冲突）
    op.execute(f"""
        INSERT INTO external_interface (id, {_SHARED_COLS}, exchanges, account_key)
        SELECT id, {_SHARED_COLS}, exchanges, account_key FROM trading_account
    """)
    op.execute(f"""
        INSERT INTO external_interface (id, {_SHARED_COLS}, exchanges, account_key)
        SELECT id, {_SHARED_COLS}, NULL, NULL FROM data_source
    """)
    op.execute("SELECT setval(pg_get_serial_sequence('external_interface', 'id'), "
               "coalesce((SELECT max(id) FROM external_interface), 1))")

    # 4. FK 重指回 external_interface.id
    for tbl, cname, col, act in _FKS:
        op.create_foreign_key(cname, tbl, "external_interface", [col], ["id"], ondelete=act)

    # 5. drop 两新表（索引随表；FK 已摘除）
    op.drop_index("ix_trading_account_account_key", table_name="trading_account")
    op.drop_index("ix_trading_account_capabilities", table_name="trading_account")
    op.drop_index("ix_trading_account_position", table_name="trading_account")
    op.drop_index("ix_trading_account_provider", table_name="trading_account")
    op.drop_table("trading_account")
    op.drop_index("ix_data_source_capabilities", table_name="data_source")
    op.drop_index("ix_data_source_position", table_name="data_source")
    op.drop_index("ix_data_source_provider", table_name="data_source")
    op.drop_table("data_source")
