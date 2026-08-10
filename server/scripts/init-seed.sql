-- 量化平台数据库初始化种子（sync_config 8 条同步任务配置）
-- ====================================================================
-- 用法:
--   psql -d quant -f scripts/init-seed.sql
--   或覆盖默认配置: psql -d quant -v ASTOCK_DAILY_SCHEDULE="0 17 * * 1-5" -f scripts/init-seed.sql
--
-- 特性:
--   - 幂等: CREATE TABLE IF NOT EXISTS + ON CONFLICT DO NOTHING，可重复执行
--   - schedule 用 cron 表达式（如 "30 16 * * 1-5" = 工作日16:30）
--   - trade_day_filter: none（不过滤）/ workday（工作日）/ trade_day（交易日，用 is_trading_day）
--   - 静态属性(api/table/mode)固定: 这些是数据类型本身的属性，不该用户改
--   - 后续可在 Web DataManage 页改 schedule/trade_day_filter，本脚本只提供首次种子默认值
-- ====================================================================

-- ============================================================
-- 可配置项（用户可改）
-- schedule: cron 表达式（分 时 日 月 周）
--   "30 16 * * 1-5" = 工作日16:30
--   "0 9 * * 1"     = 周一9:00
--   "0 9 1 1 *"     = 1月1日9:00
-- trade_day_filter: none / workday / trade_day
-- ============================================================
\set ASTOCK_DAILY_SCHEDULE    '30 16 * * 1-5'
\set ASTOCK_DAILY_FILTER      'trade_day'
\set ASTOCK_BASIC_SCHEDULE    '30 16 * * 1-5'
\set ASTOCK_BASIC_FILTER      'trade_day'
\set ASTOCK_LIST_SCHEDULE     '0 9 * * 1'
\set ASTOCK_LIST_FILTER       'none'
\set CB_DAILY_SCHEDULE        '30 16 * * 1-5'
\set CB_DAILY_FILTER          'trade_day'
\set CB_BASIC_SCHEDULE        '0 9 * * 1'
\set CB_BASIC_FILTER          'none'
\set ETF_DAILY_SCHEDULE       '30 16 * * 1-5'
\set ETF_DAILY_FILTER         'trade_day'
\set ETF_LIST_SCHEDULE        '0 9 * * 1'
\set ETF_LIST_FILTER          'none'
\set TRADE_CAL_SCHEDULE       '0 9 1 1 *'
\set TRADE_CAL_FILTER         'none'
\set ASTOCK_MINUTE_SCHEDULE   '0 16 * * 1-5'
\set ASTOCK_MINUTE_FILTER     'trade_day'


-- ============================================================
-- 建表（IF NOT EXISTS，幂等；含 trade_day_filter 列）
-- ============================================================
CREATE TABLE IF NOT EXISTS sync_config (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    tushare_api     TEXT NOT NULL,
    pg_table        TEXT NOT NULL,
    data_type       TEXT NOT NULL,
    sync_mode       TEXT NOT NULL DEFAULT 'incremental',
    schedule        TEXT NOT NULL DEFAULT 'manual',
    trade_day_filter TEXT DEFAULT 'none',
    enabled         BOOLEAN DEFAULT true,
    last_sync_date  TEXT,
    last_sync_ts    TIMESTAMPTZ,
    last_sync_count INTEGER DEFAULT 0,
    last_status     TEXT DEFAULT 'idle',
    description     TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);


-- ============================================================
-- 种子数据（8 条同步任务）
-- ON CONFLICT DO NOTHING: 已存在不覆盖（用户在 Web 改过的配置不会丢）
-- ============================================================
INSERT INTO sync_config (id, name, tushare_api, pg_table, data_type, sync_mode, schedule, trade_day_filter, enabled, description) VALUES
    ('astock_daily', 'A股日线行情',    'pro.daily',       'bar_1D',          'astock',     'incremental', :'ASTOCK_DAILY_SCHEDULE', :'ASTOCK_DAILY_FILTER', 'true', 'A股全市场日线OHLCV，按日批量拉取'),
    ('astock_basic', 'A股基本面指标',  'pro.daily_basic', 'daily_basic',     'astock',     'incremental', :'ASTOCK_BASIC_SCHEDULE', :'ASTOCK_BASIC_FILTER', 'true', 'PE/PB/市值/换手率等'),
    ('astock_list',  'A股股票列表',    'pro.stock_basic', 'asset_static_info','astock',    'full',        :'ASTOCK_LIST_SCHEDULE',  :'ASTOCK_LIST_FILTER',  'true', '全市场股票基本信息+上市日'),
    ('cb_daily',     '可转债日线行情', 'pro.cb_daily',    'bar_1D',          'convertible','incremental', :'CB_DAILY_SCHEDULE',     :'CB_DAILY_FILTER',     'true', '可转债日线OHLCV'),
    ('cb_basic',     '可转债基本信息', 'pro.cb_basic',    'cb_basic_info',   'convertible','full',        :'CB_BASIC_SCHEDULE',     :'CB_BASIC_FILTER',     'true', '可转债条款/转股价/到期日等'),
    ('etf_daily',    'ETF日线行情',    'pro.fund_daily',  'bar_1D',          'etf',        'incremental', :'ETF_DAILY_SCHEDULE',    :'ETF_DAILY_FILTER',    'true', 'ETF日线OHLCV'),
    ('etf_list',     'ETF基金列表',    'pro.fund_basic',  'etf_basic_info',  'etf',        'full',        :'ETF_LIST_SCHEDULE',     :'ETF_LIST_FILTER',     'true', 'ETF基金基本信息'),
    ('trade_cal',    '交易日历',       'pro.trade_cal',   'trade_cal',       'astock',     'full',        :'TRADE_CAL_SCHEDULE',    :'TRADE_CAL_FILTER',    'true', 'SSE交易日历，is_trading_day依据'),
    ('astock_minute',      'A股分钟线1分', 'pro.stk_mins', 'bar_1min', 'astock', 'incremental', :'ASTOCK_MINUTE_SCHEDULE', :'ASTOCK_MINUTE_FILTER', 'true', 'A股1分钟K线，per-symbol拉取（stk_mins需2000积分，全市场量大）'),
    ('astock_minute_5min', 'A股分钟线5分', 'pro.stk_mins', 'bar_5min', 'astock', 'incremental', :'ASTOCK_MINUTE_SCHEDULE', :'ASTOCK_MINUTE_FILTER', 'true', 'A股5分钟K线，per-symbol拉取')
ON CONFLICT (id) DO NOTHING;


-- ============================================================
-- 验证
-- ============================================================
\echo '✓ sync_config 种子已初始化（已存在的不覆盖）:'
SELECT id, name, schedule, trade_day_filter, enabled FROM sync_config ORDER BY id;
