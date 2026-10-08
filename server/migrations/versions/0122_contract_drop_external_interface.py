# EXPAND-CONTRACT: phase=contract pair=0116
"""批 83a：external_interface 拆表 —— **contract 步**（DROP 旧表，收口两步走）。

**承 0116（expand 步）**：0116 已建 `data_source` + `trading_account`、搬迁（原 id）、FK 重指，
旧表 `external_interface` 按两步走立法**留存**为「回滚到 pre-83a 代码」的安全垫。
本版 DROP 旧表——此刻才真的安全，判据（批85 立法核心）：

- **上一版代码（= 0116 expand 版）已 0 处实读 external_interface**，只读写新表 ⇒ 删除不破坏任何在跑代码；
- **回滚不变量仍成立**：`rollback-tasks.yml` 只回代码，回滚落点 = expand 版代码（只读 trading_account）
  ⇒ 表删了回滚照样活。这正是拆两步买来的性质——若 0116 单步 DROP，回滚落点（pre-83a 代码）实读旧表，
  应用硬崩且自动路径救不回。
- **wrapper 无时序约束**：`quant-dbro`/`quant-hbcheck` 双态选表对 `01`（仅新表）与 `11`（并存）
  同选 `trading_account` ⇒ 迁移前后 preflight / 阶段 8 都绿。

**上产通道（有意设计，非遗漏）**：阶段 4 破坏性 DDL 门会命中本版 upgrade 的 `DROP TABLE` ⇒
**本版发布必须显式 `-e allow_contract=true`**（contract 版的人工显式标记，禁静默破坏性上产）。
批84 已实查：`allow_contract` 只被 rescue(2-5) 消费、rescue(6-8) 不看 ⇒ 阶段 6-8 失败仍会自动回滚代码
——**对本版无害**：回滚落点代码不依赖旧表（见上），自动回滚反而是正确行为。

**幂等性**：staging 的旧表已由「旧版单步 0116」提前删掉（其 DB 早已 0121）⇒ 必须 `IF EXISTS`；
prod 在本版上产前旧表仍在（0116 expand 版已上产，2026-10-01）⇒ 真删。索引随表一并消失。

downgrade（0122 → 0121）：**精确重建 0115 形态的旧表**（0090 基础 12 列 + 3 索引 + 0099 account_key
+ 唯一索引），内容**以新表为真源**回填（trading_account 整行 + data_source 整行、exchanges/account_key
置 NULL）——⚠ **前置断言两域 id 不得撞号**（同 0116 downgrade：两表各持独立 BIGSERIAL，撞号时合并回
单表必主键冲突；`ON CONFLICT DO NOTHING` 会静默丢整行账号，故响亮拒绝，处置权交回人）。
**不动 7 表 FK**（0121 态它们指向 trading_account，本步前后不变；进一步降级由 0116 的 downgrade 处理）。
并存期在旧表侧的直接写入本就无人做（旧表停止写入）⇒「按新表重建」零信息丢失。

**已知设计债（沿用，本步不修）**：两新表独立序列的跨域撞号 ⇒ 见 0116 文件头与 `flow/待办.md`。

Revision ID: 0122
Revises: 0121
"""
from alembic import context, op
import sqlalchemy as sa

revision = "0122"
down_revision = "0121"

# 两域回填共用列（除 id）——与 0116 的 _SHARED_COLS 同源，防列名漂移
_SHARED_COLS = ("name, provider, market, credentials_encrypted, params, "
                "capabilities, position, enabled, created_at, updated_at")


def upgrade() -> None:
    # 索引（ix_external_interface_* 四个）依附表，随表一并消失，无需单独 drop。
    # IF EXISTS：staging 的旧表已被「旧版单步 0116」提前删除——两环境起点不同，幂等兜平。
    op.execute("DROP TABLE IF EXISTS external_interface")


def downgrade() -> None:
    # 1. **前置断言：两域 id 不得撞号**（同 0116 downgrade 的拦停，理由一致）
    if not context.is_offline_mode():
        _row = op.get_bind().execute(sa.text(
            "SELECT count(*), min(d.id) FROM data_source d JOIN trading_account t ON d.id = t.id"
        )).one()
        if _row[0]:
            raise RuntimeError(
                f"降级阻断：拆表后两域 id 撞号 {_row[0]} 行（最小示例 id={_row[1]}）。"
                "两表各持独立序列，合并回 external_interface 会主键冲突，须人工重编号后再降级。"
            )

    # 2. 重建旧表 = 0090 基础形态 + 0099 account_key（列序/默认值逐列对齐 0090 原文）
    op.create_table(
        "external_interface",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("exchanges", sa.ARRAY(sa.Text()), nullable=True),   # NULL=注册表该市场全所
        sa.Column("credentials_encrypted", sa.Text()),
        sa.Column("params", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("capabilities", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("account_key", sa.Text(), nullable=True),           # 0099：D2 语义键（资金账号）
    )
    op.create_index("ix_external_interface_provider", "external_interface", ["provider"])
    op.create_index("ix_external_interface_position", "external_interface", ["position"])
    op.create_index("ix_external_interface_capabilities", "external_interface",
                    ["capabilities"], postgresql_using="gin")
    # 0099：语义键唯一（NULL 不参与唯一判定）
    op.create_index("ix_external_interface_account_key", "external_interface",
                    ["provider", "account_key"], unique=True)

    # 3. 内容按**新表这一真源**回填（旧表已删，直接整表插入，无需 DELETE 交集）
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

    # 4. 7 表 FK **不动**：0121 态它们指向 trading_account，降级到 0121 后仍应如此
    #    （继续往 0115 降是 0116 downgrade 的职责：摘 FK → 刷新旧表 → FK 回指 → drop 两新表）。
