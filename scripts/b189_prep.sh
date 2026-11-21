#!/usr/bin/env bash
# 批189 特权准备脚本（在目标机以 root 跑）：wrapper/unit/sudoers 装位 + 全库备份 + DB preflight 映射
# 用法:  sudo bash deploy/scripts/b189_prep.sh <staging|prod> [db=quant]
# 幂等：bootstrap 为 install 覆盖；备份每次新建；b189_* 映射表 DROP/CREATE 重建。
# 前置：head=0191、无 running live_task；staging 需仓库在位（含 deploy/）；prod 需 SRC=/home/michael/3b2 已同步。
set -euo pipefail
ENV=${1:?用法: sudo bash b189_prep.sh <staging|prod> [db=quant]}
DB=${2:-quant}
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/.." && pwd)
QPSQL="sudo -u quant psql -d $DB"
[ "$(id -u)" = "0" ] || { echo "❌ 须 root 运行"; exit 2; }

echo "== [0] 目标机状态校验（head=0191、无 running live_task）=="
HEAD=$($QPSQL -tAc "select version_num from alembic_version")
[ "$HEAD" = "0191" ] || { echo "❌ head=$HEAD（期望 0191，本脚本只服务 0191→0192）"; exit 3; }
RUN=$($QPSQL -tAc "select count(*) from live_task where status='running'")
[ "$RUN" = "0" ] || { echo "❌ 有 $RUN 个 running live_task，先停任务"; exit 3; }
echo "   head=0191 run=0 OK"

# deploy 公钥路径：bootstrap_staging 默认取 $SUDO_USER 家，michael sudo 时找错家——显式解析
if [ -f /home/bernard/.ssh/quant_deploy_ed25519.pub ]; then
  PUB=/home/bernard/.ssh/quant_deploy_ed25519.pub
else
  PUB=/home/${SUDO_USER:-bernard}/.ssh/quant_deploy_ed25519.pub
fi
export PUB

echo "== [1] wrapper/unit/sudoers 装位（幂等；刷新 dbro/hbcheck TRANSITION-0189）=="
case "$ENV" in
  staging)
    if [ -f "$REPO/deploy/scripts/bootstrap_staging.sh" ]; then bash "$REPO/deploy/scripts/bootstrap_staging.sh";
    else echo "⚠ 未找到 bootstrap_staging.sh，跳过（确认 wrapper 已含 0189 分支）"; fi ;;
  prod)
    SRC=${SRC:-/home/michael/3b2}
    if [ -f "$REPO/deploy/scripts/bootstrap_server.sh" ]; then SRC="$SRC" bash "$REPO/deploy/scripts/bootstrap_server.sh";
    elif [ -f "$SRC/bootstrap_server.sh" ]; then SRC="$SRC" bash "$SRC/bootstrap_server.sh";
    else echo "⚠ 未找到 bootstrap_server.sh，跳过（确认 prod wrapper 已含 0189 分支）"; fi ;;
  *) echo "env 须 staging|prod"; exit 2;;
esac

echo "== [2] 全库备份 + 可恢复性校验 =="
BK=/var/lib/pgsql/quant_${ENV}_$(date +%F_%H%M%S).dump
sudo -u postgres pg_dump -Fc -d "$DB" -f "$BK"
sudo -u postgres pg_restore --list "$BK" >/dev/null
ls -lh "$BK"
echo "   backup OK: $BK"

echo "== [3] DB preflight 映射（自动 -N 后缀，无冲突；0192 依赖）=="
$QPSQL -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
DROP TABLE IF EXISTS b189_counts;
CREATE TABLE b189_counts(k text PRIMARY KEY, val bigint);
INSERT INTO b189_counts SELECT 'md_n', count(*) FROM trading_account WHERE 'rt_quote'=ANY(capabilities);
INSERT INTO b189_counts SELECT 'td_n', count(*) FROM trading_account WHERE 'trading'=ANY(capabilities) OR is_virtual;
INSERT INTO b189_counts SELECT 'live_task_nn', count(*) FROM live_task WHERE account_id IS NOT NULL;
INSERT INTO b189_counts SELECT 'account_snapshot_nn', count(*) FROM account_snapshot WHERE account_id IS NOT NULL;
INSERT INTO b189_counts SELECT 'order_log_nn', count(*) FROM order_log WHERE account_id IS NOT NULL;
INSERT INTO b189_counts SELECT 'usage_matched', count(*) FROM data_source_usage u WHERE EXISTS (SELECT 1 FROM data_source d WHERE d.provider=u.provider);
DROP TABLE IF EXISTS b189_code_map;
CREATE TABLE b189_code_map(old_id bigint PRIMARY KEY, md_code varchar(32), td_code varchar(32), login_key text);
INSERT INTO b189_code_map(old_id, md_code, td_code, login_key)
SELECT ta.id,
 CASE WHEN 'rt_quote'=ANY(ta.capabilities) THEN 'md-'||replace(lower(ta.provider),'_','-')||CASE WHEN row_number() OVER (PARTITION BY ta.provider ORDER BY ta.id)>1 THEN '-'||row_number() OVER (PARTITION BY ta.provider ORDER BY ta.id) ELSE '' END END,
 CASE WHEN 'trading'=ANY(ta.capabilities) OR ta.is_virtual THEN 'td-'||replace(lower(ta.provider),'_','-')||CASE WHEN row_number() OVER (PARTITION BY ta.provider ORDER BY ta.id)>1 THEN '-'||row_number() OVER (PARTITION BY ta.provider ORDER BY ta.id) ELSE '' END END,
 ta.provider||'|'||COALESCE(ta.account_key, CASE WHEN ta.is_virtual THEN 'virtual' WHEN ta.credentials_encrypted IS NULL THEN 'public' ELSE 'legacy' END)
FROM trading_account ta;
DROP TABLE IF EXISTS b189_ds_map;
CREATE TABLE b189_ds_map(old_id bigint PRIMARY KEY, code varchar(32) NOT NULL);
INSERT INTO b189_ds_map SELECT id, 'ds-'||replace(lower(provider),'_','-')||CASE WHEN row_number() OVER (PARTITION BY provider ORDER BY id)>1 THEN '-'||row_number() OVER (PARTITION BY provider ORDER BY id) ELSE '' END FROM data_source;
DROP TABLE IF EXISTS b189_slot_backup;
CREATE TABLE b189_slot_backup AS SELECT * FROM provider_session_slot;
DROP TABLE IF EXISTS b189_td_mapping_expect;
CREATE TABLE b189_td_mapping_expect AS SELECT DISTINCT t.td_code FROM b189_code_map t JOIN trading_account ta ON ta.id=t.old_id JOIN trading_account q ON q.id=ta.quote_account_id WHERE t.td_code IS NOT NULL AND NOT ta.is_virtual AND 'rt_quote'=ANY(q.capabilities);
SQL
echo "   maps:"; $QPSQL -tAc "select 'md='||(select count(*) from b189_code_map where md_code is not null)||' td='||(select count(*) from b189_code_map where td_code is not null)||' ds='||(select count(*) from b189_ds_map)"

echo ""
echo "✅ PREP DONE env=$ENV db=$DB backup=$BK"
echo "→ 接下来由 bernard 控制机跑:"
echo "   cd $REPO/deploy && .venv/bin/ansible-playbook -i inventory/quant-$ENV.yml playbooks/release.yml -e allow_contract=true -e contract_reason=\"批189 M1-M5 接口三表拆表(offline-atomic)\""
