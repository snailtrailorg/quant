-- 量化平台数据库 schema 集中初始化（新库一次跑，避免 CREATE TABLE 分散导致的鸡生蛋）
-- ====================================================================
-- 用法: psql -d quant -f scripts/init-schema.sql
-- 特性:
--   - 幂等: CREATE TABLE IF NOT EXISTS，可重复执行
--   - 所有表 OWNER = quant（避免 owner=postgres 的权限问题）
--   - 含所有业务表: users/audit_log/sync_config/sync_log/bar_1D/daily_basic/asset_static_info/cb_basic_info/etf_basic_info/trade_cal
--   - 各 handler 的 CREATE TABLE IF NOT EXISTS 保留作运行时兜底，本脚本是主初始化
-- ====================================================================

-- 连接 quant 库
\connect quant

-- ============================================================
-- 1. users（认证）
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE users OWNER TO quant;

-- ============================================================
-- 2. audit_log（审计日志，含 old_value/new_value）
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    old_value TEXT,
    new_value TEXT,
    ts TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE audit_log OWNER TO quant;

-- ============================================================
-- 3. sync_config（同步任务配置，种子由 init-seed.sql 插入）
-- ============================================================
CREATE TABLE IF NOT EXISTS sync_config (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    tushare_api     TEXT NOT NULL,
    pg_table        TEXT NOT NULL,
    data_type       TEXT NOT NULL,
    sync_mode       TEXT NOT NULL DEFAULT 'incremental',
    schedule        TEXT NOT NULL DEFAULT 'manual',
    enabled         BOOLEAN DEFAULT true,
    last_sync_date  TEXT,
    last_sync_ts    TIMESTAMPTZ,
    last_sync_count INTEGER DEFAULT 0,
    last_status     TEXT DEFAULT 'idle',
    description     TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE sync_config OWNER TO quant;

-- ============================================================
-- 4. sync_log（同步日志，含 failed_dates/expected_days/actual_days）
-- ============================================================
CREATE TABLE IF NOT EXISTS sync_log (
    id BIGSERIAL PRIMARY KEY,
    sync_id TEXT NOT NULL,
    ts TIMESTAMPTZ DEFAULT now(),
    mode TEXT,
    start_date TEXT,
    end_date TEXT,
    rows_pulled INTEGER DEFAULT 0,
    rows_saved INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    error TEXT,
    failed_dates TEXT,
    expected_days INTEGER,
    actual_days INTEGER
);
ALTER TABLE sync_log OWNER TO quant;

-- ============================================================
-- 5. bar_1D（K线，统一 schema 对齐 XTP 实时）
-- ============================================================
CREATE TABLE IF NOT EXISTS bar_1D (
    id        BIGSERIAL PRIMARY KEY,
    symbol    TEXT NOT NULL,
    freq      TEXT NOT NULL DEFAULT '1D',
    ts        TIMESTAMPTZ NOT NULL,
    open      NUMERIC NOT NULL,
    high      NUMERIC NOT NULL,
    low       NUMERIC NOT NULL,
    close     NUMERIC NOT NULL,
    volume    NUMERIC NOT NULL DEFAULT 0,
    amount    NUMERIC NOT NULL DEFAULT 0,
    adj_factor NUMERIC,
    source    TEXT NOT NULL DEFAULT 'tushare',
    UNIQUE(symbol, ts)
);
CREATE INDEX IF NOT EXISTS idx_bar_1d_symbol_ts ON bar_1D (symbol, ts DESC);
ALTER TABLE bar_1D OWNER TO quant;
ALTER INDEX idx_bar_1d_symbol_ts OWNER TO quant;

-- ============================================================
-- 6. daily_basic（A股基本面：PE/PB/市值/换手率）
-- ============================================================
CREATE TABLE IF NOT EXISTS daily_basic (
    id BIGSERIAL PRIMARY KEY,
    ts_code TEXT NOT NULL,
    vt_symbol TEXT NOT NULL,
    trade_date DATE NOT NULL,
    close NUMERIC,
    turnover_rate NUMERIC,
    pe NUMERIC,
    pe_ttm NUMERIC,
    pb NUMERIC,
    ps NUMERIC,
    ps_ttm NUMERIC,
    dv_ratio NUMERIC,
    dv_ttm NUMERIC,
    total_mv NUMERIC,
    circ_mv NUMERIC,
    UNIQUE(ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_basic_ts_code ON daily_basic (ts_code, trade_date DESC);
ALTER TABLE daily_basic OWNER TO quant;
ALTER INDEX idx_daily_basic_ts_code OWNER TO quant;

-- ============================================================
-- 7. asset_static_info（A股股票列表 + 上市日）
-- ============================================================
CREATE TABLE IF NOT EXISTS asset_static_info (
    ts_code TEXT PRIMARY KEY,
    name TEXT,
    industry TEXT,
    market TEXT,
    list_status TEXT,
    list_date TEXT,
    delist_date TEXT
);
ALTER TABLE asset_static_info OWNER TO quant;

-- ============================================================
-- 8. cb_basic_info（可转债基本信息）
-- ============================================================
CREATE TABLE IF NOT EXISTS cb_basic_info (
    ts_code TEXT PRIMARY KEY,
    bond_short_name TEXT,
    stk_code TEXT,
    stk_short_name TEXT,
    maturity TEXT,
    par NUMERIC,
    issue_price NUMERIC,
    conv_price NUMERIC,
    conv_start_date TEXT,
    conv_end_date TEXT,
    maturity_date TEXT,
    coupon_rate NUMERIC,
    rate_clause TEXT,
    list_date TEXT,
    delist_date TEXT
);
ALTER TABLE cb_basic_info OWNER TO quant;

-- ============================================================
-- 9. etf_basic_info（ETF基金列表）
-- ============================================================
CREATE TABLE IF NOT EXISTS etf_basic_info (
    ts_code TEXT PRIMARY KEY,
    name TEXT,
    management TEXT,
    fund_type TEXT,
    invest_type TEXT,
    list_date TEXT
);
ALTER TABLE etf_basic_info OWNER TO quant;

-- ============================================================
-- 10. trade_cal（交易日历）
-- ============================================================
CREATE TABLE IF NOT EXISTS trade_cal (
    exchange TEXT,
    cal_date DATE,
    is_open INT,
    pretrade_date DATE,
    PRIMARY KEY(exchange, cal_date)
);
ALTER TABLE trade_cal OWNER TO quant;

-- ============================================================
-- 验证
-- ============================================================
\echo '✓ schema 初始化完成（所有表 OWNER=quant）:'
SELECT tablename, tableowner FROM pg_tables WHERE schemaname='public' ORDER BY tablename;
