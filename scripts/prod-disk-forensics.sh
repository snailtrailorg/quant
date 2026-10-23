#!/usr/bin/env bash
# prod 磁盘取证（批 143 A 段）——只读，产出落 stdout（建议重定向文件带回）
# 用法（michael@server）：sudo bash prod-disk-forensics.sh > /tmp/forensics-out.txt 2>&1
# 五问清单见 flow/任务/批143-prod磁盘治理-恢复演练先行.md §A3；本脚本零写入零删除。
set -uo pipefail
echo "===== 0. 基线 $(date '+%F %T') ====="
df -h / /data /var 2>/dev/null | sort -u
echo; echo "===== 1. PG 目录分解 ====="
PGDATA="$(su - postgres -c 'psql -tAc "show data_directory"' 2>/dev/null || psql -tAc 'show data_directory' 2>/dev/null)"
echo "PGDATA=$PGDATA"
du -x -d1 "$PGDATA" 2>/dev/null | sort -rh | head -15
echo "-- base 每库（目录名=oid，对照 pg_database）--"
du -x -d1 "$PGDATA/base" 2>/dev/null | sort -rh | head -12
echo "-- pg_wal --"
du -sh "$PGDATA/pg_wal" 2>/dev/null
ls "$PGDATA/pg_wal" 2>/dev/null | grep -c '^[-0-9A-F]\{24\}$' | xargs echo 'wal 段数:'
echo "-- temp/log --"
du -sh "$PGDATA/pg_wal/summaries" 2>/dev/null
find "$PGDATA" -name 'pgsql_tmp*' -o -name '*.log' 2>/dev/null | head -5
echo; echo "===== 2. 每库大小（SQL 口径）====="
su - postgres -c "psql -tAc \"select datname||' :: '||pg_size_pretty(pg_database_size(datname)) from pg_database where datallowconn order by pg_database_size(datname) desc\"" 2>/dev/null
echo; echo "===== 3. quant 库 top20 表（total size）====="
su - postgres -c "psql -d quant -tAc \"select c.relname||' :: '||pg_size_pretty(pg_total_relation_size(c.oid)) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r' order by pg_total_relation_size(c.oid) desc limit 20\"" 2>/dev/null
echo; echo "===== 4. quant 库死元组 top20 ====="
su - postgres -c "psql -d quant -tAc \"select relname||' :: live='||n_live_tup||' dead='||n_dead_tup||' dead%='||round(n_dead_tup*100.0/greatest(n_live_tup,1),1)||' autovac='||coalesce(last_autovacuum::text,'never') from pg_stat_user_tables order by n_dead_tup desc limit 20\"" 2>/dev/null
echo; echo "===== 5. replication slots / archiver ====="
su - postgres -c "psql -tAc \"select slot_name||' type='||slot_type||' active='||active||' retained='||coalesce(pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(),restart_lsn)),'-') from pg_replication_slots\"" 2>/dev/null
echo "(空=无 slot)"
su - postgres -c "psql -tAc \"select 'archived='||archived_count||' failed='||failed_count||' last_fail='||coalesce(last_failed_wal::text,'-')||' '||coalesce(last_failed_time::text,'-') from pg_stat_archiver\"" 2>/dev/null
echo; echo "===== 6. temp 文件统计 ====="
su - postgres -c "psql -tAc \"select datname||' files='||temp_files||' bytes='||pg_size_pretty(temp_bytes) from pg_stat_database where temp_files>0\"" 2>/dev/null
echo "(空=无 temp)"
echo; echo "===== 7. quant 部署根分解 ====="
du -x -d2 /data/websites/snailtrail.cc/quant 2>/dev/null | sort -rh | head -30
echo; echo "===== 8. 备份目录 ====="
ls -lh /home/quant/backups 2>/dev/null | tail -10
echo; echo "===== 9. 服务日志大头（journal 磁盘占用）====="
journalctl --disk-usage 2>/dev/null
echo "===== done ====="
