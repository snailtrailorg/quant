#!/usr/bin/env bash
# 批189 M1 验证 harness：重建 scratch → 灌 dev schema/数据 → 预检产物 → 0192 → V1–V10
# 用法: bash scripts/b189_m1_harness.sh [scratch_db=quant_189_dev]
set -uo pipefail
DB="${1:-quant_189_dev}"
SRC_DB="quant"
PG="psql -U quant"
cd "$(dirname "$0")/.."

echo "== 1. 重建 $DB =="
$PG -d $SRC_DB -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null || exit 2
$PG -d $SRC_DB -c "CREATE DATABASE $DB" >/dev/null || exit 2
pg_dump -U quant -d $SRC_DB --schema-only | grep -v "CREATE EXTENSION" > /tmp/b189_schema.sql
$PG -d $DB -q -f /tmp/b189_schema.sql 2>&1 | grep -v "extension \"vector\"" | tail -2
$PG -d $SRC_DB -Fc -t trading_account -t data_source -t data_source_usage \
  -t live_task -t position_snapshot -t position_refresh -t account_snapshot \
  -t order_log -t trade_log -t account_permission -t provider_session_slot \
  -f /tmp/b189_dev_tables.dump
pg_restore -U quant -d $DB --data-only /tmp/b189_dev_tables.dump 2>&1 | tail -2
$PG -d $DB -q -c "INSERT INTO alembic_version(version_num) SELECT '0191' WHERE NOT EXISTS (SELECT 1 FROM alembic_version)"

echo "== 2. 预检产物 =="
$PG -d $DB -v ON_ERROR_STOP=1 <<'SQL'
DROP TABLE IF EXISTS b189_counts;
CREATE TABLE b189_counts(k text PRIMARY KEY, val bigint);
INSERT INTO b189_counts
SELECT 'md_n', count(*) FROM trading_account WHERE 'rt_quote' = ANY(capabilities);
INSERT INTO b189_counts
SELECT 'td_n', count(*) FROM trading_account WHERE 'trading' = ANY(capabilities) OR is_virtual;
INSERT INTO b189_counts SELECT 'live_task_nn', count(*) FROM live_task WHERE account_id IS NOT NULL;
INSERT INTO b189_counts SELECT 'account_snapshot_nn', count(*) FROM account_snapshot WHERE account_id IS NOT NULL;
INSERT INTO b189_counts SELECT 'order_log_nn', count(*) FROM order_log WHERE account_id IS NOT NULL;
INSERT INTO b189_counts
SELECT 'usage_matched', count(*) FROM data_source_usage u
 WHERE EXISTS (SELECT 1 FROM data_source d WHERE d.provider=u.provider);

DROP TABLE IF EXISTS b189_code_map;
CREATE TABLE b189_code_map(old_id bigint PRIMARY KEY, md_code varchar(32), td_code varchar(32), login_key text);
INSERT INTO b189_code_map(old_id, md_code, td_code, login_key)
SELECT ta.id,
       CASE WHEN 'rt_quote' = ANY(ta.capabilities) THEN
         'md-'||replace(lower(ta.provider),'_','-')||
         CASE WHEN row_number() OVER (PARTITION BY ta.provider ORDER BY ta.id) > 1
              THEN '-'||row_number() OVER (PARTITION BY ta.provider ORDER BY ta.id) ELSE '' END
       END,
       CASE WHEN 'trading' = ANY(ta.capabilities) OR ta.is_virtual THEN
         'td-'||replace(lower(ta.provider),'_','-')||
         CASE WHEN row_number() OVER (PARTITION BY ta.provider ORDER BY ta.id) > 1
              THEN '-'||row_number() OVER (PARTITION BY ta.provider ORDER BY ta.id) ELSE '' END
       END,
       ta.provider||'|'||COALESCE(ta.account_key,
         CASE WHEN ta.is_virtual THEN 'virtual'
              WHEN ta.credentials_encrypted IS NULL THEN 'public' ELSE 'legacy' END)
FROM trading_account ta;

DROP TABLE IF EXISTS b189_ds_map;
CREATE TABLE b189_ds_map(old_id bigint PRIMARY KEY, code varchar(32) NOT NULL);
INSERT INTO b189_ds_map
SELECT id, 'ds-'||replace(lower(provider),'_','-')||
       CASE WHEN row_number() OVER (PARTITION BY provider ORDER BY id) > 1
            THEN '-'||row_number() OVER (PARTITION BY provider ORDER BY id) ELSE '' END
FROM data_source;

DROP TABLE IF EXISTS b189_slot_backup;
CREATE TABLE b189_slot_backup AS SELECT * FROM provider_session_slot;

DROP TABLE IF EXISTS b189_td_mapping_expect;
CREATE TABLE b189_td_mapping_expect AS
SELECT DISTINCT t.td_code FROM b189_code_map t
JOIN trading_account ta ON ta.id=t.old_id
JOIN trading_account q ON q.id=ta.quote_account_id
WHERE t.td_code IS NOT NULL AND NOT ta.is_virtual AND 'rt_quote' = ANY(q.capabilities);
SQL

echo "== 3. alembic upgrade 0192 =="
(cd server && QUANT_DB_URL="postgresql://quant@/$DB" ./venv/bin/alembic upgrade head 2>&1 | tail -4)

echo "== 4. 断言 V1–V10 =="
$PG -d $DB -v ON_ERROR_STOP=1 -q -f scripts/verify_b189_migration.sql || { echo "ASSERT FAILED"; exit 1; }

echo "== 5. 摘要 =="
$PG -d $DB -tA -F' | ' -c "select 'md',count(*) from market_interface union all select 'td',count(*) from trade_interface union all select 'map',count(*) from td_md_map union all select 'ds',count(*) from data_source"
echo "OK"
