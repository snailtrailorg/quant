"""批55a：external_interface 表（方案一 v2 终裁——行=账号/列=能力/页签=过滤视图）。

合并 data_source_config + broker_config（27 号架构文档；批43 channel_config 同版 drop 先例）。
列语义：
- capabilities text[] NOT NULL = 用户启用子集（⊆ 代码能力 provider_capabilities——写侧端点校验）
- market text NOT NULL = MARKETS 注册表键（astock/crypto）
- exchanges text[] NULL = 包含式覆盖集合（NULL=注册表该市场全所；excluded 机制退役）。
  存量数据行写 NULL 保持全所语义（盲审 A-P2：显式数组会冻结注册表快照，未来新所静默不含）
- position int = 分域选行序（trading∈capabilities=交易域，否则数据域；域内独立 0..n）
- params jsonb = 运维参数（rate_limits/circuit_breaker/地址等；原两表 Text JSON 升 jsonb）

id 映射表（audit 追溯——存量行少，映射=(provider,name) join 可重建）：
  data_source_config(1, tushare, 'Tushare主')    → external_interface 首个数据域行
  broker_config(1, xtp, '中泰XTP测试')           → external_interface 首个交易域行
幂等（批43 P0-1 教训）：两段 INSERT 均 WHERE NOT EXISTS (provider,name)——重放零重复。
usage_limit 列不迁移（勘察发现 1：运行期零消费死列；后端透传同批删）。

盲审修订（A-P2/B-P2）：
- 升级前置 DO 块①：存量 params 垃圾 Text（旧端点零校验直插）逐行试 cast，失败置 NULL
  （告警可从 alembic 日志追）——防部署日炸在 ::jsonb
- DO 块②守门=PROVIDER_MARKET 全集（含 tencent——新端点可合法建，防 downgrade 往返被堵）
- 存量 caps 诚实化：joinquant/ricequant=stub（代码能力空）→ 空数组（防漂移告警+不可保存行）
- downgrade 分家按 provider 集合（行的边界=账号）：交易 provider 行全落 broker_config
  （含 quote-only xtp 行），其余落 data_source_config——re-upgrade 不被守门拒

Revision ID: 0090
Revises: 0089
"""
from alembic import op
import sqlalchemy as sa

revision = "0090"
down_revision = "0089"

# 守门/分家集合（=markets.PROVIDER_MARKET 键集的 SQL 镜像；加 provider 两处同改）
_TRADING_PROVIDERS = "('xtp','binance_perp','okx_perp')"
_ALL_PROVIDERS = "('tushare','joinquant','ricequant','tencent','xtp','binance_perp','okx_perp')"


def upgrade() -> None:
    # 前置①：存量 params 垃圾 Text 清 NULL（旧端点写 params 零校验；cast 失败即垃圾）
    op.execute(f"""
        DO $$
        DECLARE r record; bad int := 0;
        BEGIN
            FOR r IN SELECT id, params FROM data_source_config WHERE params IS NOT NULL AND params <> ''
            LOOP
                BEGIN
                    PERFORM (r.params)::jsonb;
                EXCEPTION WHEN OTHERS THEN
                    UPDATE data_source_config SET params = NULL WHERE id = r.id;
                    bad := bad + 1;
                    RAISE NOTICE 'data_source_config(%) params 非法 JSON 已置 NULL: %', r.id, r.params;
                END;
            END LOOP;
            FOR r IN SELECT id, params FROM broker_config WHERE params IS NOT NULL AND params <> ''
            LOOP
                BEGIN
                    PERFORM (r.params)::jsonb;
                EXCEPTION WHEN OTHERS THEN
                    UPDATE broker_config SET params = NULL WHERE id = r.id;
                    bad := bad + 1;
                    RAISE NOTICE 'broker_config(%) params 非法 JSON 已置 NULL: %', r.id, r.params;
                END;
            END LOOP;
            IF bad > 0 THEN
                RAISE NOTICE '共 % 行垃圾 params 置 NULL', bad;
            END IF;
        END $$;
    """)
    # 前置②：未映射 provider 守门（防静默错 market）
    op.execute(f"""
        DO $$
        DECLARE p text;
        BEGIN
            SELECT provider INTO p FROM data_source_config
             WHERE provider NOT IN {_ALL_PROVIDERS} LIMIT 1;
            IF p IS NOT NULL THEN
                RAISE EXCEPTION 'data_source_config 未映射 provider=%（先在 markets.py PROVIDER_MARKET 注册）', p;
            END IF;
            SELECT provider INTO p FROM broker_config
             WHERE provider NOT IN {_ALL_PROVIDERS} LIMIT 1;
            IF p IS NOT NULL THEN
                RAISE EXCEPTION 'broker_config 未映射 provider=%（先在 markets.py PROVIDER_MARKET 注册）', p;
            END IF;
        END $$;
    """)
    op.create_table(
        "external_interface",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),          # tushare/tencent/xtp/binance_perp/...
        sa.Column("market", sa.Text(), nullable=False),            # MARKETS 键（astock/crypto）
        sa.Column("exchanges", sa.ARRAY(sa.Text()), nullable=True),   # NULL=注册表该市场全所（包含式）
        sa.Column("credentials_encrypted", sa.Text()),             # Fernet 加密（token/账号 JSON）
        sa.Column("params", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("capabilities", sa.ARRAY(sa.Text()), nullable=False),   # 启用子集 ⊆ 代码能力
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_external_interface_provider", "external_interface", ["provider"])
    op.create_index("ix_external_interface_position", "external_interface", ["position"])
    op.create_index("ix_external_interface_capabilities", "external_interface",
                    ["capabilities"], postgresql_using="gin")

    # 存量迁移①：数据源行 → 数据域。caps 按代码能力真源给全集（stub 诚实给空数组）
    op.execute("""
        INSERT INTO external_interface
            (name, provider, market, exchanges, credentials_encrypted, params,
             capabilities, position, enabled, created_at, updated_at)
        SELECT d.name, d.provider, 'astock', NULL,
               d.credentials_encrypted, d.params::jsonb,
               CASE d.provider WHEN 'tushare' THEN ARRAY['daily','minute']::text[]
                               WHEN 'tencent' THEN ARRAY['minute','snapshot']::text[]
                               ELSE ARRAY[]::text[] END,
               (row_number() OVER (ORDER BY d.id))::int - 1,
               d.enabled, d.created_at, d.updated_at
        FROM data_source_config d
        WHERE NOT EXISTS (SELECT 1 FROM external_interface e
                          WHERE e.provider = d.provider AND e.name = d.name)
    """)
    # 存量迁移②：交易行 → 交易域（xtp 双能力 trading+quote；加密双 perp 交易所单所覆盖）
    op.execute("""
        INSERT INTO external_interface
            (name, provider, market, exchanges, credentials_encrypted, params,
             capabilities, position, enabled, created_at, updated_at)
        SELECT b.name, b.provider,
               CASE b.provider WHEN 'xtp' THEN 'astock' ELSE 'crypto' END,
               CASE b.provider WHEN 'binance_perp' THEN ARRAY['BINANCE']::text[]
                               WHEN 'okx_perp'   THEN ARRAY['OKX']::text[] END,
               b.credentials_encrypted, b.params::jsonb,
               CASE b.provider WHEN 'xtp' THEN ARRAY['trading','quote']::text[]
                               ELSE ARRAY['trading']::text[] END,
               (row_number() OVER (ORDER BY b.id))::int - 1,
               b.enabled, b.created_at, b.updated_at
        FROM broker_config b
        WHERE NOT EXISTS (SELECT 1 FROM external_interface e
                          WHERE e.provider = b.provider AND e.name = b.name)
    """)

    # 用量表键升级（方案一 v2：data_source_usage 键改引 interface id；provider 保留=显示冗余双填）
    op.add_column("data_source_usage",
                  sa.Column("interface_id", sa.BigInteger(), nullable=True))
    op.execute("""
        UPDATE data_source_usage u SET interface_id = e.id
        FROM external_interface e
        WHERE e.provider = u.provider
          AND e.id = (SELECT min(e2.id) FROM external_interface e2 WHERE e2.provider = u.provider)
    """)

    op.drop_index("ix_data_source_provider", table_name="data_source_config")
    op.drop_table("data_source_config")
    op.drop_index("ix_broker_provider", table_name="broker_config")
    op.drop_table("broker_config")


def downgrade() -> None:
    # 还原两旧表。分家按 provider 集合（盲审 A-P2/B-P2：行的边界=账号——交易 provider 行
    # 含 quote-only 形态全落 broker_config，防 re-upgrade 被守门拒）
    op.create_table(
        "data_source_config",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text()),
        sa.Column("params", sa.Text()),
        sa.Column("usage_limit", sa.Integer()),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_data_source_provider", "data_source_config", ["provider"])
    op.create_table(
        "broker_config",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("credentials_encrypted", sa.Text()),
        sa.Column("params", sa.Text()),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_broker_provider", "broker_config", ["provider"])
    op.execute(f"""
        INSERT INTO data_source_config (provider, name, credentials_encrypted, params, enabled, created_at, updated_at)
        SELECT provider, name, credentials_encrypted, params::text, enabled, created_at, updated_at
        FROM external_interface WHERE provider NOT IN {_TRADING_PROVIDERS}
    """)
    op.execute(f"""
        INSERT INTO broker_config (provider, name, credentials_encrypted, params, enabled, created_at, updated_at)
        SELECT provider, name, credentials_encrypted, params::text, enabled, created_at, updated_at
        FROM external_interface WHERE provider IN {_TRADING_PROVIDERS}
    """)
    op.drop_column("data_source_usage", "interface_id")
    op.drop_index("ix_external_interface_capabilities", table_name="external_interface")
    op.drop_index("ix_external_interface_position", table_name="external_interface")
    op.drop_index("ix_external_interface_provider", table_name="external_interface")
    op.drop_table("external_interface")
