"""批189：接口三表 + code 身份（offline 单事务迁移）

M0 v2 §2 / M1 v4 §3。依赖前置产物：
  b189_code_map(old_id bigint PK, md_code varchar(32), td_code varchar(32), login_key text)
  b189_slot_backup（slot 旧行留证）
缺失即 RAISE。
"""
from alembic import op

revision = "0192"
down_revision = "0191"
branch_labels = None
depends_on = None


def _sql(s: str) -> None:
    op.execute(s)


def upgrade() -> None:
    # —— 0. 防呆：preflight 产物必须在位 ——
    _sql("""
    DO $$
    BEGIN
      IF to_regclass('b189_code_map') IS NULL THEN
        RAISE EXCEPTION 'b189_code_map 缺失（先跑 preflight）';
      END IF;
      IF to_regclass('b189_slot_backup') IS NULL THEN
        RAISE EXCEPTION 'b189_slot_backup 缺失（slot 旧行未留证）';
      END IF;
      IF EXISTS (SELECT 1 FROM trading_account ta
                 LEFT JOIN b189_code_map m ON m.old_id = ta.id
                 WHERE m.old_id IS NULL) THEN
        RAISE EXCEPTION '有 trading_account 行缺 code 映射';
      END IF;
      IF EXISTS (SELECT 1 FROM trading_account ta JOIN b189_code_map m ON m.old_id=ta.id
                 WHERE ('rt_quote' = ANY(ta.capabilities)) AND m.md_code IS NULL) THEN
        RAISE EXCEPTION 'rt_quote 行缺 md_code';
      END IF;
      IF EXISTS (SELECT 1 FROM trading_account ta JOIN b189_code_map m ON m.old_id=ta.id
                 WHERE (('trading' = ANY(ta.capabilities)) OR ta.is_virtual) AND m.td_code IS NULL) THEN
        RAISE EXCEPTION 'trading/虚拟行缺 td_code';
      END IF;
    END $$;
    """)

    # —— 1. 快照（含全部列）——
    _sql("""
    DROP TABLE IF EXISTS _b189_src;
    CREATE TEMP TABLE _b189_src AS
    SELECT ta.id AS old_id, ta.name, ta.provider, ta.market, ta.account_key,
           ta.credentials_encrypted, ta.params, ta.impl, ta.position, ta.enabled,
           ta.exchanges, ta.capabilities, COALESCE(ta.is_virtual,false) AS is_virtual,
           ta.quote_account_id, ta.created_at, ta.updated_at,
           m.md_code, m.td_code, m.login_key
    FROM trading_account ta JOIN b189_code_map m ON m.old_id = ta.id;
    """)

    # —— 2. 删依赖：FK / PK / 触发器 ——
    _sql("""
    ALTER TABLE live_task          DROP CONSTRAINT IF EXISTS fk_live_task_account;
    ALTER TABLE position_snapshot  DROP CONSTRAINT IF EXISTS fk_position_snapshot_account;
    ALTER TABLE position_refresh   DROP CONSTRAINT IF EXISTS fk_position_refresh_account;
    ALTER TABLE account_snapshot   DROP CONSTRAINT IF EXISTS fk_account_snapshot_account;
    ALTER TABLE order_log          DROP CONSTRAINT IF EXISTS fk_order_log_account;
    ALTER TABLE trade_log          DROP CONSTRAINT IF EXISTS fk_trade_log_account;
    ALTER TABLE account_permission DROP CONSTRAINT IF EXISTS account_permission_account_id_fkey;
    DROP TRIGGER IF EXISTS trg_live_task_account_guard ON live_task;
    DROP FUNCTION IF EXISTS live_task_account_guard();
    """)
    _sql("""
    DO $$
    DECLARE r record;
    BEGIN
      FOR r IN SELECT c.conrelid::regclass AS tbl, c.conname
               FROM pg_constraint c
               WHERE c.contype='p'
                 AND c.conrelid IN ('position_snapshot'::regclass,'position_refresh'::regclass,
                                    'account_permission'::regclass)
      LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname);
      END LOOP;
    END $$;
    """)

    # —— 3. 新表 ——
    _sql("""
    CREATE TABLE market_interface (
      code varchar(32) COLLATE "C" PRIMARY KEY CHECK (code LIKE 'md-%'),
      name text NOT NULL, provider text NOT NULL, market text NOT NULL,
      account_key text, login_key text NOT NULL,
      credentials_encrypted text, params jsonb,
      impl text NOT NULL DEFAULT 'legacy', position int NOT NULL DEFAULT 0,
      enabled bool DEFAULT true, exchanges text[],
      created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now()
    );
    CREATE TABLE trade_interface (
      code varchar(32) COLLATE "C" PRIMARY KEY CHECK (code LIKE 'td-%'),
      name text NOT NULL, provider text NOT NULL, market text NOT NULL,
      account_key text, login_key text NOT NULL,
      credentials_encrypted text, params jsonb,
      impl text NOT NULL DEFAULT 'legacy', position int NOT NULL DEFAULT 0,
      enabled bool DEFAULT true, is_virtual bool DEFAULT false,
      created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now()
    );
    CREATE TABLE td_md_map (
      td_code varchar(32) COLLATE "C" REFERENCES trade_interface(code) ON DELETE CASCADE,
      md_code varchar(32) COLLATE "C" REFERENCES market_interface(code) ON DELETE CASCADE,
      priority int NOT NULL,
      PRIMARY KEY (td_code, md_code)
    );
    """)

    # —— 4. data_source 改造（rename + 索引/序列改名，避免同名冲突）——
    _sql("ALTER TABLE data_source RENAME TO data_source_old")
    _sql("""
    DO $$
    DECLARE r record;
    BEGIN
      FOR r IN SELECT indexname FROM pg_indexes WHERE tablename='data_source_old'
      LOOP
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.indexname, 'old_'||r.indexname);
      END LOOP;
      IF to_regclass('data_source_id_seq') IS NOT NULL THEN
        ALTER SEQUENCE data_source_id_seq RENAME TO data_source_old_id_seq;
      END IF;
    END $$;
    """)
    _sql("""
    CREATE TABLE data_source (
      code varchar(32) COLLATE "C" PRIMARY KEY CHECK (code LIKE 'ds-%'),
      name text NOT NULL, provider text NOT NULL, market text NOT NULL,
      credentials_encrypted text, params jsonb,
      capabilities text[] NOT NULL,
      impl text NOT NULL DEFAULT 'legacy',
      position int NOT NULL DEFAULT 0, enabled bool DEFAULT true,
      created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now()
    );
    INSERT INTO data_source (code,name,provider,market,credentials_encrypted,params,
                             capabilities,position,enabled,created_at,updated_at)
    SELECT m.code, d.name, d.provider, d.market,
           d.credentials_encrypted, d.params, d.capabilities, d.position, d.enabled,
           d.created_at, d.updated_at
    FROM data_source_old d JOIN b189_ds_map m ON m.old_id = d.id;
    CREATE INDEX ix_data_source_provider ON data_source(provider);
    CREATE INDEX ix_data_source_position ON data_source(position);
    """)

    # —— 5. 灌 md/td ——
    _sql("""
    INSERT INTO market_interface (code,name,provider,market,account_key,login_key,
      credentials_encrypted,params,impl,position,enabled,exchanges,created_at,updated_at)
    SELECT md_code,name,provider,market,account_key,login_key,
           credentials_encrypted,params,impl,position,enabled,exchanges,created_at,updated_at
    FROM _b189_src WHERE md_code IS NOT NULL;
    INSERT INTO trade_interface (code,name,provider,market,account_key,login_key,
      credentials_encrypted,params,impl,position,enabled,is_virtual,created_at,updated_at)
    SELECT td_code,name,provider,market,account_key,login_key,
           credentials_encrypted,params,impl,position,enabled,is_virtual,created_at,updated_at
    FROM _b189_src WHERE td_code IS NOT NULL;
    """)

    # —— 6. 映射初值 ——
    _sql("""
    INSERT INTO td_md_map (td_code, md_code, priority)
    SELECT t.td_code, m.md_code, 1
    FROM _b189_src t JOIN _b189_src m ON m.old_id = t.quote_account_id
    WHERE t.td_code IS NOT NULL AND m.md_code IS NOT NULL AND NOT t.is_virtual;
    """)

    # 7. 业务列
    _sql("""
    ALTER TABLE live_task         ADD COLUMN account_code varchar(32);
    ALTER TABLE position_snapshot ADD COLUMN account_code varchar(32);
    ALTER TABLE position_refresh  ADD COLUMN account_code varchar(32);
    ALTER TABLE account_snapshot  ADD COLUMN account_code varchar(32);
    ALTER TABLE order_log         ADD COLUMN account_code varchar(32);
    ALTER TABLE trade_log         ADD COLUMN account_code varchar(32);
    ALTER TABLE freeze_event      ADD COLUMN account_code varchar(32);
    ALTER TABLE data_source_usage ADD COLUMN interface_code varchar(32);
    """)
    _sql("""
    UPDATE live_task t SET account_code=s.td_code FROM _b189_src s WHERE t.account_id=s.old_id AND s.td_code IS NOT NULL;
    UPDATE position_snapshot t SET account_code=s.td_code FROM _b189_src s WHERE t.account_id=s.old_id AND s.td_code IS NOT NULL;
    UPDATE position_refresh t SET account_code=s.td_code FROM _b189_src s WHERE t.account_id=s.old_id AND s.td_code IS NOT NULL;
    UPDATE account_snapshot t SET account_code=s.td_code FROM _b189_src s WHERE t.account_id=s.old_id AND s.td_code IS NOT NULL;
    UPDATE order_log t SET account_code=s.td_code FROM _b189_src s WHERE t.account_id=s.old_id AND s.td_code IS NOT NULL;
    UPDATE trade_log t SET account_code=s.td_code FROM _b189_src s WHERE t.account_id=s.old_id AND s.td_code IS NOT NULL;
    UPDATE freeze_event t SET account_code=s.td_code FROM _b189_src s WHERE t.account_id=s.old_id AND s.td_code IS NOT NULL;
    UPDATE data_source_usage u SET interface_code=d.code FROM data_source d WHERE d.provider=u.provider;
    """)
    _sql("""
    DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM live_task WHERE account_code IS NULL) THEN RAISE EXCEPTION 'live_task.account_code 有 NULL'; END IF;
      IF EXISTS (SELECT 1 FROM account_snapshot WHERE account_code IS NULL) THEN RAISE EXCEPTION 'account_snapshot.account_code 有 NULL'; END IF;
      IF EXISTS (SELECT 1 FROM position_snapshot WHERE account_code IS NULL) THEN RAISE EXCEPTION 'position_snapshot.account_code 有 NULL'; END IF;
      IF EXISTS (SELECT 1 FROM position_refresh WHERE account_code IS NULL) THEN RAISE EXCEPTION 'position_refresh.account_code 有 NULL'; END IF;
    END $$;
    ALTER TABLE live_task         ALTER COLUMN account_code SET NOT NULL;
    ALTER TABLE account_snapshot  ALTER COLUMN account_code SET NOT NULL;
    ALTER TABLE position_snapshot ALTER COLUMN account_code SET NOT NULL;
    ALTER TABLE position_refresh  ALTER COLUMN account_code SET NOT NULL;
    """)

    # 8. account_permission 重建（login_key 归属，无 FK）
    _sql("""
    CREATE TABLE account_permission_new (
      login_key text PRIMARY KEY,
      allowed_categories text[] NOT NULL, allowed_exchanges text[] NOT NULL,
      allowed_boards text[] NOT NULL, is_st_allowed bool NOT NULL,
      convertible_allowed bool NOT NULL,
      created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now());
    INSERT INTO account_permission_new (login_key,allowed_categories,allowed_exchanges,
      allowed_boards,is_st_allowed,convertible_allowed,created_at,updated_at)
    SELECT DISTINCT ON (s.login_key) s.login_key, ap.allowed_categories, ap.allowed_exchanges,
           ap.allowed_boards, ap.is_st_allowed, ap.convertible_allowed, ap.created_at, ap.updated_at
    FROM account_permission ap JOIN _b189_src s ON s.old_id = ap.account_id
    WHERE s.login_key IS NOT NULL
    ORDER BY s.login_key, ap.updated_at DESC NULLS LAST;
    DROP TABLE account_permission;
    ALTER TABLE account_permission_new RENAME TO account_permission;
    """)

    # 9. PK / FK / UK
    _sql("""
    ALTER TABLE position_snapshot ADD CONSTRAINT pk_position_snapshot PRIMARY KEY (account_code, symbol, direction);
    ALTER TABLE position_refresh  ADD CONSTRAINT pk_position_refresh  PRIMARY KEY (account_code);
    ALTER TABLE live_task         ADD CONSTRAINT fk_live_task_account
      FOREIGN KEY (account_code) REFERENCES trade_interface(code) ON DELETE RESTRICT;
    ALTER TABLE position_snapshot ADD CONSTRAINT fk_position_snapshot_account
      FOREIGN KEY (account_code) REFERENCES trade_interface(code) ON DELETE CASCADE;
    ALTER TABLE position_refresh  ADD CONSTRAINT fk_position_refresh_account
      FOREIGN KEY (account_code) REFERENCES trade_interface(code) ON DELETE CASCADE;
    ALTER TABLE account_snapshot  ADD CONSTRAINT fk_account_snapshot_account
      FOREIGN KEY (account_code) REFERENCES trade_interface(code) ON DELETE CASCADE;
    ALTER TABLE order_log         ADD CONSTRAINT fk_order_log_account
      FOREIGN KEY (account_code) REFERENCES trade_interface(code) ON DELETE SET NULL;
    ALTER TABLE trade_log         ADD CONSTRAINT fk_trade_log_account
      FOREIGN KEY (account_code) REFERENCES trade_interface(code) ON DELETE SET NULL;
    ALTER TABLE data_source_usage ADD CONSTRAINT fk_dsu_interface_code
      FOREIGN KEY (interface_code) REFERENCES data_source(code) ON DELETE SET NULL;
    CREATE UNIQUE INDEX uq_md_market_account_key ON market_interface (market, account_key)
      WHERE account_key IS NOT NULL AND enabled;
    CREATE UNIQUE INDEX uq_td_market_account_key ON trade_interface (market, account_key)
      WHERE account_key IS NOT NULL AND enabled;
    CREATE INDEX ix_live_task_account_code ON live_task(account_code);
    CREATE INDEX ix_position_snapshot_account_code ON position_snapshot(account_code);
    CREATE INDEX ix_account_snapshot_account_code ON account_snapshot(account_code);
    CREATE INDEX ix_order_log_account_code ON order_log(account_code);
    CREATE INDEX ix_trade_log_account_code ON trade_log(account_code);
    """)

    # 10. 触发器
    _sql("""
    CREATE OR REPLACE FUNCTION live_task_account_guard() RETURNS trigger AS $fn$
    DECLARE v_virtual boolean;
    BEGIN
      SELECT is_virtual INTO v_virtual FROM trade_interface WHERE code = NEW.account_code;
      IF v_virtual IS NULL THEN
        RAISE EXCEPTION 'live_task.account_code=% 不存在于 trade_interface', NEW.account_code;
      END IF;
      IF NEW.mode = 'live' AND v_virtual THEN
        RAISE EXCEPTION '实盘任务(mode=live)不得绑定虚拟账户(code=%)', NEW.account_code;
      END IF;
      RETURN NEW;
    END;
    $fn$ LANGUAGE plpgsql;
    CREATE TRIGGER trg_live_task_account_guard
    BEFORE INSERT OR UPDATE OF account_code, mode ON live_task
    FOR EACH ROW EXECUTE FUNCTION live_task_account_guard();
    """)

    # 11. 租约表（不迁旧行）
    _sql("""
    DROP TABLE provider_session_slot;
    CREATE TABLE provider_session_slot (
      login_key text NOT NULL, provider text NOT NULL,
      role text NOT NULL CHECK (role IN ('md','td')),
      terminal_id int NOT NULL, holder text NOT NULL, boot_epoch bigint NOT NULL,
      heartbeat timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (login_key, provider, role, terminal_id));
    """)

    # 11b. jsonb 类型守卫 CHECK（保持 0191 data_source 的 ck_*_params_object 语义；新表同款）
    #      —— test_jsonb_columns_guarded 守门：jsonb 静默降级(json.dumps 双重编码)须被 23514 拦。
    _sql("""
    ALTER TABLE data_source      ADD CONSTRAINT ck_data_source_params_object
      CHECK (params IS NULL OR jsonb_typeof(params)='object');
    ALTER TABLE market_interface ADD CONSTRAINT ck_market_interface_params_object
      CHECK (params IS NULL OR jsonb_typeof(params)='object');
    ALTER TABLE trade_interface  ADD CONSTRAINT ck_trade_interface_params_object
      CHECK (params IS NULL OR jsonb_typeof(params)='object');
    """)

    # 12. 删旧
    _sql("""
    ALTER TABLE live_task         DROP COLUMN account_id;
    ALTER TABLE position_snapshot DROP COLUMN account_id;
    ALTER TABLE position_refresh  DROP COLUMN account_id;
    ALTER TABLE account_snapshot  DROP COLUMN account_id;
    ALTER TABLE order_log         DROP COLUMN account_id;
    ALTER TABLE trade_log         DROP COLUMN account_id;
    ALTER TABLE freeze_event      DROP COLUMN account_id;
    ALTER TABLE data_source_usage DROP COLUMN interface_id;
    DROP TABLE trading_account;
    DROP TABLE data_source_old;
    """)

    # 13. 断言（失败即回滚整事务）
    _sql("""
    DO $$
    DECLARE n int;
    BEGIN
      SELECT count(*) INTO n FROM market_interface WHERE code !~ '^[a-z][a-z0-9-]{1,30}$';
      IF n>0 THEN RAISE EXCEPTION 'md code 不合正则: %', n; END IF;
      SELECT count(*) INTO n FROM trade_interface WHERE code !~ '^[a-z][a-z0-9-]{1,30}$';
      IF n>0 THEN RAISE EXCEPTION 'td code 不合正则: %', n; END IF;
      SELECT count(*) INTO n FROM live_task WHERE account_code IS NOT NULL
        AND account_code NOT IN (SELECT code FROM trade_interface);
      IF n>0 THEN RAISE EXCEPTION 'live_task 孤儿 account_code: %', n; END IF;
      IF to_regclass('trading_account') IS NOT NULL THEN RAISE EXCEPTION 'trading_account 未删'; END IF;
      IF EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name IN ('live_task','order_log','trade_log','position_snapshot',
                                      'position_refresh','account_snapshot','freeze_event')
                   AND column_name='account_id') THEN
        RAISE EXCEPTION '仍有 account_id 列';
      END IF;
    END $$;
    """)


def downgrade() -> None:
    raise RuntimeError("0192 不可逆：回滚=恢复迁移前快照")
