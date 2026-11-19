#!/usr/bin/env bash
# 批189 M1 生产 preflight：只读检查 + （--apply）落 preflight 产物
# 用法:
#   bash scripts/b189_m1_preflight.sh [--db quant] [--apply] [--codes codes.tsv] [--ds-codes ds.tsv]
# codes.tsv:   old_id<TAB>md_code<TAB>td_code   （空值写 -）
# ds.tsv:      old_id<TAB>code
# 退出码: 0=可执行 / 2=参数或环境 / 3=发现阻断项（未解决冲突）
set -uo pipefail
DB="quant"; APPLY=0; CODES=""; DSCODES=""
while [ $# -gt 0 ]; do
  case "$1" in
    --db) DB="$2"; shift 2;;
    --apply) APPLY=1; shift;;
    --codes) CODES="$2"; shift 2;;
    --ds-codes) DSCODES="$2"; shift 2;;
    *) echo "未知参数 $1"; exit 2;;
  esac
done
P="psql -U quant -d $DB -tA -F'|'"
q() { psql -U quant -d $DB -tA -F'|' -c "$1"; }

echo "== A. 环境 =="
HEAD=$(q "select version_num from alembic_version")
[ "$HEAD" = "0191" ] && echo "   head=0191 OK" || { echo "   ✗ head=$HEAD（期望 0191）"; exit 3; }
RUN=$(q "select count(*) from live_task where status='running'")
[ "$RUN" = "0" ] && echo "   running live_task=0 OK" || { echo "   ✗ 有 $RUN 个 running 任务"; exit 3; }

echo "== B. 存量形态 =="
echo "   trading_account 行数: $(q 'select count(*) from trading_account')"
echo "   virtual 行: $(q 'select count(*) from trading_account where is_virtual')"
BAD_CAP=$(q "select count(*) from trading_account
  where not is_virtual and not ('trading'=ANY(capabilities)) and not ('rt_quote'=ANY(capabilities))")
[ "$BAD_CAP" = "0" ] && echo "   capabilities 异常=0 OK" || { echo "   ✗ capabilities 异常 $BAD_CAP 行"; exit 3; }

echo "== C. account_key 策略 =="
q "select provider, count(*) filter (where account_key is null) as nulls,
          count(*) filter (where coalesce(is_virtual,false)) as virt
   from trading_account group by provider order by provider" | while IFS='|' read -r P NULLS VIRT; do
  echo "   provider=$P null_key=$NULLS virtual=$VIRT"
done
echo "== D. ds provider 分布 =="
q "select provider, count(*) from data_source group by provider order by provider" | while IFS='|' read -r P N; do
  echo "   ds provider=$P rows=$N"
done
echo "== E. usage provider 覆盖 =="
q "select count(*) from data_source_usage u
   where not exists (select 1 from data_source d where d.provider=u.provider)" | sed 's/^/   无 ds 行的 usage: /'
echo "== F. quote 目标无 md 产出（允许，将无映射） =="
q "select ta.id, ta.provider from trading_account ta
   join trading_account q on q.id=ta.quote_account_id
   where not ta.is_virtual and not ('rt_quote'=ANY(q.capabilities))" | sed 's/^/   无 md: /'
echo "== G. code 预演冲突 =="
CONFLICT=$(q "with g as (
  select 'td' role, provider from trading_account where 'trading'=ANY(capabilities) or is_virtual
  union all
  select 'md', provider from trading_account where 'rt_quote'=ANY(capabilities))
  select count(*) from (select role, provider from g group by 1,2 having count(*)>1) x")
DSDUP=$(q "select count(*) from (select provider from data_source group by provider having count(*)>1) x")
echo "   md/td 同 provider 冲突组: $CONFLICT ; ds 同 provider 冲突组: $DSDUP"
if [ "$CONFLICT" != "0" ] || [ "$DSDUP" != "0" ]; then
  echo "   ⚠ 存在同名冲突——必须提供 --codes / --ds-codes 人工定名（不自动编号）"
  if [ -z "$CODES" ] || [ -z "$DSCODES" ]; then
    echo "   生成预览："
    q "select id, provider, capabilities, coalesce(account_key,'') from trading_account order by id" | sed 's/^/     ta  /'
    q "select id, provider, name from data_source order by id" | sed 's/^/     ds  /'
    exit 3
  fi
fi

if [ "$APPLY" != "1" ]; then echo "== 干跑结束（未写库；加 --apply 落产物）=="; exit 0; fi

echo "== H. 落产物 =="
psql -U quant -d $DB -v ON_ERROR_STOP=1 <<'SQL'
DROP TABLE IF EXISTS b189_counts;
CREATE TABLE b189_counts(k text PRIMARY KEY, val bigint);
INSERT INTO b189_counts SELECT 'md_n', count(*) FROM trading_account WHERE 'rt_quote'=ANY(capabilities);
INSERT INTO b189_counts SELECT 'td_n', count(*) FROM trading_account WHERE 'trading'=ANY(capabilities) OR is_virtual;
INSERT INTO b189_counts SELECT 'live_task_nn', count(*) FROM live_task WHERE account_id IS NOT NULL;
INSERT INTO b189_counts SELECT 'account_snapshot_nn', count(*) FROM account_snapshot WHERE account_id IS NOT NULL;
INSERT INTO b189_counts SELECT 'order_log_nn', count(*) FROM order_log WHERE account_id IS NOT NULL;
INSERT INTO b189_counts SELECT 'usage_matched', count(*) FROM data_source_usage u
 WHERE EXISTS (SELECT 1 FROM data_source d WHERE d.provider=u.provider);

DROP TABLE IF EXISTS b189_slot_backup;
CREATE TABLE b189_slot_backup AS SELECT * FROM provider_session_slot;

DROP TABLE IF EXISTS b189_td_mapping_expect;
CREATE TABLE b189_td_mapping_expect AS
SELECT DISTINCT 'td-'||replace(lower(ta.provider),'_','-') AS td_code
FROM trading_account ta JOIN trading_account q ON q.id=ta.quote_account_id
WHERE NOT ta.is_virtual AND 'rt_quote'=ANY(q.capabilities) AND ('trading'=ANY(ta.capabilities));
SQL
echo "   counts/slot_backup/mapping_expect 已落"

if [ -n "$CODES" ]; then
  psql -U quant -d $DB -v ON_ERROR_STOP=1 -c "DROP TABLE IF EXISTS b189_code_map; CREATE TABLE b189_code_map(old_id bigint PRIMARY KEY, md_code varchar(32), td_code varchar(32), login_key text);"
  psql -U quant -d $DB -v ON_ERROR_STOP=1 -c "\copy b189_code_map(old_id,md_code,td_code) FROM '$CODES' WITH (FORMAT csv, DELIMITER E'\t', NULL '-')"
  psql -U quant -d $DB -v ON_ERROR_STOP=1 -c "UPDATE b189_code_map m SET login_key =
     (SELECT ta.provider||'|'||COALESCE(ta.account_key, CASE WHEN ta.is_virtual THEN 'virtual'
        WHEN ta.credentials_encrypted IS NULL THEN 'public' ELSE 'legacy' END)
      FROM trading_account ta WHERE ta.id=m.old_id)"
else
  psql -U quant -d $DB -v ON_ERROR_STOP=1 <<'SQL'
DROP TABLE IF EXISTS b189_code_map;
CREATE TABLE b189_code_map(old_id bigint PRIMARY KEY, md_code varchar(32), td_code varchar(32), login_key text);
INSERT INTO b189_code_map(old_id, md_code, td_code, login_key)
SELECT ta.id,
  CASE WHEN 'rt_quote'=ANY(ta.capabilities) THEN 'md-'||replace(lower(ta.provider),'_','-') END,
  CASE WHEN 'trading'=ANY(ta.capabilities) OR ta.is_virtual THEN 'td-'||replace(lower(ta.provider),'_','-') END,
  ta.provider||'|'||COALESCE(ta.account_key, CASE WHEN ta.is_virtual THEN 'virtual'
     WHEN ta.credentials_encrypted IS NULL THEN 'public' ELSE 'legacy' END)
FROM trading_account ta;
SQL
fi

if [ -n "$DSCODES" ]; then
  psql -U quant -d $DB -v ON_ERROR_STOP=1 -c "DROP TABLE IF EXISTS b189_ds_map; CREATE TABLE b189_ds_map(old_id bigint PRIMARY KEY, code varchar(32) NOT NULL);"
  psql -U quant -d $DB -v ON_ERROR_STOP=1 -c "\copy b189_ds_map(old_id,code) FROM '$DSCODES' WITH (FORMAT csv, DELIMITER E'\t')"
else
  psql -U quant -d $DB -v ON_ERROR_STOP=1 <<'SQL'
DROP TABLE IF EXISTS b189_ds_map;
CREATE TABLE b189_ds_map(old_id bigint PRIMARY KEY, code varchar(32) NOT NULL);
INSERT INTO b189_ds_map SELECT id, 'ds-'||replace(lower(provider),'_','-') FROM data_source;
SQL
fi

echo "== I. 产物校验 =="
psql -U quant -d $DB -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM b189_code_map WHERE md_code IS NOT NULL
    AND md_code !~ '^[a-z][a-z0-9-]{1,30}$';
  IF n>0 THEN RAISE EXCEPTION 'md_code 非法 %', n; END IF;
  SELECT count(*) INTO n FROM b189_code_map WHERE td_code IS NOT NULL
    AND td_code !~ '^[a-z][a-z0-9-]{1,30}$';
  IF n>0 THEN RAISE EXCEPTION 'td_code 非法 %', n; END IF;
  SELECT count(*) INTO n FROM (
    SELECT code FROM (SELECT md_code AS code FROM b189_code_map WHERE md_code IS NOT NULL
      UNION ALL SELECT td_code FROM b189_code_map WHERE td_code IS NOT NULL) u
    GROUP BY code HAVING count(*)>1) z;
  IF n>0 THEN RAISE EXCEPTION 'code 跨表重复 %', n; END IF;
  SELECT count(*) INTO n FROM b189_ds_map WHERE code !~ '^[a-z][a-z0-9-]{1,30}$';
  IF n>0 THEN RAISE EXCEPTION 'ds code 非法 %', n; END IF;
  IF EXISTS (SELECT 1 FROM trading_account ta LEFT JOIN b189_code_map m ON m.old_id=ta.id WHERE m.old_id IS NULL)
  THEN RAISE EXCEPTION '有行缺 code 映射'; END IF;
END $$;
SQL
RC=$?
if [ "$RC" != "0" ]; then echo "   ✗ 产物校验失败 rc=$RC"; exit 4; fi
echo "   OK"
