-- 批189 M1 迁移耗时基线（在 prod 的 scratch 库里跑，绝不指向 quant）
-- 用途：用真实数据量校准 0192 的 40 min 红线（只测量，不产出迁移结果）
\set ON_ERROR_STOP on
\timing on

DO $$ BEGIN
  IF current_database() NOT LIKE '%scratch%' THEN
    RAISE EXCEPTION '拒绝执行：当前库=% 不是 scratch 库', current_database();
  END IF;
END $$;

\echo '== 0. 数据量基线 =='
SELECT relname,
       to_char(n_live_tup, 'FM999,999,999') AS rows,
       pg_size_pretty(pg_total_relation_size(relid)) AS size
FROM pg_stat_user_tables
WHERE relname IN ('trading_account','live_task','position_snapshot','position_refresh',
                  'account_snapshot','order_log','trade_log','account_permission','data_source_usage')
ORDER BY pg_total_relation_size(relid) DESC;

\echo '== 1. 加列（PG11+ 仅改元数据，应亚秒级）=='
ALTER TABLE live_task            ADD COLUMN IF NOT EXISTS account_code TEXT;
ALTER TABLE position_snapshot    ADD COLUMN IF NOT EXISTS account_code TEXT;
ALTER TABLE position_refresh     ADD COLUMN IF NOT EXISTS account_code TEXT;
ALTER TABLE account_snapshot     ADD COLUMN IF NOT EXISTS account_code TEXT;
ALTER TABLE order_log            ADD COLUMN IF NOT EXISTS account_code TEXT;
ALTER TABLE trade_log            ADD COLUMN IF NOT EXISTS account_code TEXT;

\echo '== 2. 映射表（模拟拆分：id -> td-<provider>-<id>）=='
DROP TABLE IF EXISTS _b189_map;
CREATE TABLE _b189_map AS
  SELECT id, 'td-' || provider || '-' || id AS code FROM trading_account;
CREATE UNIQUE INDEX ON _b189_map(id);
ANALYZE _b189_map;

\echo '== 3. 回填 UPDATE（真正的耗时大头）=='
UPDATE live_task         t SET account_code = m.code FROM _b189_map m WHERE t.account_id = m.id;
UPDATE position_snapshot t SET account_code = m.code FROM _b189_map m WHERE t.account_id = m.id;
UPDATE position_refresh  t SET account_code = m.code FROM _b189_map m WHERE t.account_id = m.id;
UPDATE account_snapshot  t SET account_code = m.code FROM _b189_map m WHERE t.account_id = m.id;
UPDATE order_log         t SET account_code = m.code FROM _b189_map m WHERE t.account_id = m.id;
UPDATE trade_log         t SET account_code = m.code FROM _b189_map m WHERE t.account_id = m.id;

\echo '== 4. 索引 + FK（第二个大头）=='
CREATE INDEX ON live_task(account_code);
CREATE INDEX ON position_snapshot(account_code);
CREATE INDEX ON position_refresh(account_code);
CREATE INDEX ON account_snapshot(account_code);
CREATE INDEX ON order_log(account_code);
CREATE INDEX ON trade_log(account_code);

\echo '== 5. 结果核对 =='
SELECT 'live_task' t, count(*) FILTER (WHERE account_code IS NOT NULL) done, count(*) total FROM live_task
UNION ALL SELECT 'order_log', count(*) FILTER (WHERE account_code IS NOT NULL), count(*) FROM order_log
UNION ALL SELECT 'trade_log', count(*) FILTER (WHERE account_code IS NOT NULL), count(*) FROM trade_log;

\echo '== 6. 收尾（scratch 库可留作 0192 实测）=='
DROP TABLE _b189_map;
