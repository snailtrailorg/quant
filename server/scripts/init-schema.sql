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
-- 11. account_snapshot（账户快照/盈亏）
-- ============================================================
CREATE TABLE IF NOT EXISTS account_snapshot (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT now(),
    total_value NUMERIC NOT NULL,
    daily_pnl NUMERIC DEFAULT 0,
    initial_capital NUMERIC NOT NULL DEFAULT 1000000
);
ALTER TABLE account_snapshot OWNER TO quant;

-- ============================================================
-- 12. accounts（交易所账户，含加密 API key/secret）
-- ============================================================
CREATE TABLE IF NOT EXISTS accounts (
    id SERIAL PRIMARY KEY,
    name TEXT,
    exchange TEXT NOT NULL,
    api_key_enc TEXT,
    api_secret_enc TEXT,
    api_key_hint TEXT,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE accounts OWNER TO quant;

-- ============================================================
-- 13. alert_history（告警历史）
-- ============================================================
CREATE TABLE IF NOT EXISTS alert_history (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT now(),
    level TEXT,
    title TEXT,
    body TEXT,
    channel TEXT
);
ALTER TABLE alert_history OWNER TO quant;

-- ============================================================
-- 14. astock_analysis（A股分析结果）
-- ============================================================
CREATE TABLE IF NOT EXISTS astock_analysis (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT now(),
    symbol TEXT,
    action TEXT,
    score NUMERIC,
    rating TEXT,
    factors JSONB
);
ALTER TABLE astock_analysis OWNER TO quant;

-- ============================================================
-- 15. broker_usage（券商调用统计）
-- ============================================================
CREATE TABLE IF NOT EXISTS broker_usage (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT now(),
    provider TEXT,
    action TEXT,
    symbol TEXT,
    success BOOLEAN,
    latency_ms INTEGER
);
ALTER TABLE broker_usage OWNER TO quant;

-- ============================================================
-- 16. convertible_terms（可转债条款）
-- ============================================================
CREATE TABLE IF NOT EXISTS convertible_terms (
    ts_code TEXT PRIMARY KEY,
    terms JSONB,
    updated_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE convertible_terms OWNER TO quant;

-- ============================================================
-- 17. signal_log（策略信号）
-- ============================================================
CREATE TABLE IF NOT EXISTS signal_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT now(),
    strategy_id TEXT,
    symbol TEXT,
    action TEXT,
    score NUMERIC,
    price NUMERIC
);
ALTER TABLE signal_log OWNER TO quant;

-- ============================================================
-- 18. order_log（订单）
-- ============================================================
CREATE TABLE IF NOT EXISTS order_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT now(),
    strategy_id TEXT,
    symbol TEXT,
    action TEXT,
    volume INTEGER,
    price NUMERIC,
    status TEXT DEFAULT 'submitted',
    signal_id BIGINT
);
ALTER TABLE order_log OWNER TO quant;

-- ============================================================
-- 19. trade_log（成交）
-- ============================================================
CREATE TABLE IF NOT EXISTS trade_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT now(),
    order_id BIGINT,
    symbol TEXT,
    action TEXT,
    volume INTEGER,
    price NUMERIC,
    commission NUMERIC
);
ALTER TABLE trade_log OWNER TO quant;

-- ============================================================
-- 20. static_symbols（静态标的列表）
-- ============================================================
CREATE TABLE IF NOT EXISTS static_symbols (
    ts_code TEXT PRIMARY KEY,
    name TEXT,
    industry TEXT,
    list_status TEXT,
    delisted BOOLEAN DEFAULT false,
    updated_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE static_symbols OWNER TO quant;

-- ============================================================
-- 验证
-- ============================================================
\echo '✓ schema 初始化完成（所有表 OWNER=quant）:'
SELECT tablename, tableowner FROM pg_tables WHERE schemaname='public' ORDER BY tablename;
