#!/usr/bin/env bash
# 备份恢复演练（批 143 A 段）——dump → 临时库 restore → 三级验证升级版 → 临时库自删
# 用法（root 或 sudo）: bash backup-restore-drill.sh <dump 文件路径> [临时库名=quant_drill]
# 风险闸: 开跑前自查 df——restore 需要与解压后 dump 相当的剩余空间；临时库用后即删，其余零删除。
# 磁盘余量不足时 abort（restore 一步前检查），绝不硬跑。
set -uo pipefail
DUMP="${1:?用法: backup-restore-drill.sh <dump路径> [临时库名]}"
DBNAME="${2:-quant_drill}"
FAIL=0
chk() { if [ "$2" = "$3" ]; then echo "  ✓ $1: $2"; else echo "  ✗ $1: 期望[$3] 实得[$2]"; FAIL=1; fi; }
psqlq() { su - postgres -c "psql -d $DBNAME -tAc \"$1\"" 2>&1; }

echo "===== 0. 前置 ====="
[ -f "$DUMP" ] || { echo "✗ dump 不存在: $DUMP"; exit 2; }
ls -lh "$DUMP"
FREE_KB=$(df --output=avail -k /var/lib/pgsql 2>/dev/null | tail -1 | tr -dc 0-9)
echo "PG 所在盘剩余: $((FREE_KB/1024)) MB"
echo; echo "===== 1. 三级验证之一：gzip 完整性 ====="
gzip -t "$DUMP" && echo "  ✓ gzip -t OK" || { echo "  ✗ gzip -t FAIL"; FAIL=1; }
echo; echo "===== 2. 三级验证之二：结构段计数 ====="
CT=$(zcat "$DUMP" | grep -c '^CREATE TABLE ' || true)
CP=$(zcat "$DUMP" | grep -c '^COPY ' || true)
echo "  CREATE TABLE 段: $CT / COPY 数据段: $CP"
[ "$CT" -gt 100 ] || { echo "  ✗ 段数异常偏少"; FAIL=1; }
echo; echo "===== 3. 空间闸（按解压后估算）====="
UNCOMP_MB=$(zcat "$DUMP" | wc -c | awk '{print int($1/1024/1024)+200}')
echo "解压后估算: ${UNCOMP_MB} MB"
[ "$FREE_KB" -gt $((UNCOMP_MB*1024)) ] || { echo "  ✗ 空间不足，abort（restore 不硬跑）"; exit 3; }
echo; echo "===== 4. restore 正身（临时库 $DBNAME）====="
su - postgres -c "dropdb --if-exists $DBNAME" && echo "  (旧临时库已清)"
su - postgres -c "createdb $DBNAME" || { echo "✗ createdb 失败"; exit 3; }
zcat "$DUMP" | su - postgres -c "psql -d $DBNAME -q -v ON_ERROR_STOP=0" >/tmp/drill-restore.log 2>&1
RC=$?
echo "  restore 退出码: $RC（非零看 /tmp/drill-restore.log 尾部）"
tail -5 /tmp/drill-restore.log
ERR=$(grep -c 'ERROR' /tmp/drill-restore.log || true); echo "  ERROR 行数: $ERR"
echo; echo "===== 5. 三级验证之三：对数（dump COPY 段行数 vs 临时库实数）====="
zcat "$DUMP" | awk '/^COPY /{t=$2;n=0;f=1;next} /^\\\.$/{if(f){print t,n;f=0}next} f{n++}' > /tmp/drill-copylines.txt
TOTAL_SEG=$(wc -l < /tmp/drill-copylines.txt)
echo "  dump 数据段 $TOTAL_SEG 个；关键表对数："
for T in public.sync_config public.security_master public.users public.bar_1d; do
  A=$(awk -v t="$T " '$1==t{print $2}' /tmp/drill-copylines.txt | head -1)
  B=$(psqlq "select count(*) from ${T#public.}" 2>/dev/null)
  chk "$T" "$B" "$A"
done
echo; echo "===== 6. 游标实抽（sync_config，与 dump 时点=02:00 对齐人工判读）====="
psqlq "select id||' :: '||coalesce(last_sync_date::text,'-')||' '||coalesce(last_sync_count::text,'-')||' '||last_status from sync_config order by id" 2>/dev/null | head -20
echo; echo "===== 7. 临时库大小 + 自删 ====="
su - postgres -c "psql -tAc \"select pg_size_pretty(pg_database_size('$DBNAME'))\"" 2>/dev/null
su - postgres -c "dropdb $DBNAME" && echo "  ✓ 临时库已删"
echo; [ "$FAIL" = 0 ] && echo "=== 演练全部通过 ===" || echo "=== 有失败项 ==="
exit $FAIL
