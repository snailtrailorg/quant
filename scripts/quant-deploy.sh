#!/bin/bash
########################################################################################################################
# quant-deploy.sh - 部署工具 for 多市场混合量化交易平台
# 部署到新服务器（quant.snailtrail.cc），与 safebox 共存时严格隔离（db4/5/6 避开 safebox db0）：
#   - clear-redis 只清 db2(VALKEY)/db3(CELERY)，绝不用 FLUSHALL（会清掉 safebox db0）
#   - restart-web 用 reload httpd（不断现有连接，不影响 safebox）
#   - clear-pgsql 严格 DROP DATABASE quant（不碰 safebox 库）
#   - 不改 pg_hba.conf（适配 safebox 已有的 md5 认证）
#
# Usage: $0 --server HOST [--user USER] [--dry-run] ACTION...
#
# 安装: cp scripts/quant-deploy.sh ~/.local/bin/quant-deploy.sh && chmod +x ~/.local/bin/quant-deploy.sh
# 项目内便捷脚本 deploy-server.sh / deploy-web.sh 通过 `sudo -u michael quant-deploy.sh` 调用本脚本。
########################################################################################################################

set -euo pipefail

#-----------------------------------------------------------------------------------------------------------------------
# 服务器配置（MODIFY TO MATCH YOUR SERVER）—— 单一配置源：改环境只动这里
#-----------------------------------------------------------------------------------------------------------------------
SERVER_DOMAIN="${SERVER_DOMAIN:-quant.snailtrail.cc}"            # 服务器域名/IP
SITE_ROOT="${SITE_ROOT:-/data/websites/snailtrail.cc/quant}"      # 站点根（server/ + web/）
PROJECT_PATH="${PROJECT_PATH:-$SITE_ROOT/server}"                # 后端代码+venv+.env
WEB_PATH="${WEB_PATH:-$SITE_ROOT/web}"                           # 前端 dist
SEED_SQL="${SEED_SQL:-$PROJECT_PATH/scripts/init-seed.sql}"
SCHEMA_SQL="${SCHEMA_SQL:-$PROJECT_PATH/scripts/init-schema.sql}"

# 运行身份 + 端口（service 文件 + health 检查共用，单一来源）
QUANT_USER="${QUANT_USER:-quant}"                                # service User= + venv owner
WEB_API_PORT="${WEB_API_PORT:-8001}"                             # uvicorn 监听端口

# systemd 服务名（模板式 @quant 实例）
WEB_API_SERVICE="${WEB_API_SERVICE:-quant-web-api@quant}"
CELERY_WORKER_SERVICE="${CELERY_WORKER_SERVICE:-quant-celery-worker@quant}"
CELERY_BEAT_SERVICE="${CELERY_BEAT_SERVICE:-quant-celery-beat@quant}"
CELERY_RISK_SERVICE="${CELERY_RISK_SERVICE:-quant-celery-risk@quant}"   # SE2: risk 队列专属，防饿死（F-48）
MD_HUB_SERVICE="${MD_HUB_SERVICE:-quant-md-hub@quant}"                 # ST7: 共享行情 hub
PGSQL_SERVICE="${PGSQL_SERVICE:-postgresql-18}"
REDIS_SERVICE="${REDIS_SERVICE:-redis}"
WEB_SERVICE="${WEB_SERVICE:-nginx}"

# systemd/polkit 安装目标
SYSTEMD_DST="${SYSTEMD_DST:-/etc/systemd/system}"
POLKIT_DST="${POLKIT_DST:-/etc/polkit-1/rules.d}"
SYSTEMD_SRC="${SYSTEMD_SRC:-$PROJECT_PATH/scripts/systemd}"      # 源码里的 service 模板

# 数据库
PG_DB="${PG_DB:-quant}"
PG_USER="${PG_USER:-quant}"

# Redis db 分配（对齐服务器 .env 实际值：safebox=db0/db1，quant=业务db4/broker db5/result db6）
# 2026-08-17 修正：原默认 db2/db3 与服务器实际不符，clear-redis 会清错库
VALKEY_DB="${VALKEY_DB:-4}"      # VALKEY_URL：心跳锁/熔断/JWT黑名单/去重
CELERY_DB="${CELERY_DB:-5}"      # CELERY_BROKER_URL
CELERY_RESULT_DB="${CELERY_RESULT_DB:-6}"  # CELERY_RESULT_BACKEND

# Redis CLI（safebox 用 redis6-cli）
REDIS_CLI="${REDIS_CLI:-/usr/bin/redis-cli}"

#-----------------------------------------------------------------------------------------------------------------------
# SSH
#-----------------------------------------------------------------------------------------------------------------------
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"

#-----------------------------------------------------------------------------------------------------------------------
# 命令行 + 运行时状态
#-----------------------------------------------------------------------------------------------------------------------
USER=""
SERVER="${SERVER:-$SERVER_DOMAIN}"    # 默认域名，可用环境变量或 --server 覆盖
DRY_RUN=false

DEPLOY_TASKS=()
RESTART_SERVER=false
RESTART_CELERY=false
RESTART_HUB=false
RESTART_WEB=false
RESTART_FEISHU=false
RESTART_PGSQL=false
RESTART_REDIS=false
FIX_VENV=false
INSTALL_SERVICES=false
ENABLE_SERVICES=false
CLEAR_PGSQL=false
CLEAR_REDIS=false
INIT_SEED=false
INIT_SCHEMA=false
MIGRATE=false
PIP_INSTALL=false
HAS_ACTION=false

#-----------------------------------------------------------------------------------------------------------------------
usage() {
    cat <<EOF
Usage: $0 --server HOST [--user USER] [--dry-run] ACTION...

部署代码、重启服务、初始化数据库到远程服务器（与 safebox 隔离）。

Global options:
  --server HOST          目标服务器（默认 quant.snailtrail.cc，可用环境变量 SERVER 覆盖）
  --user USER            SSH 用户（默认当前用户）
  --dry-run              预览不执行
  -h, --help

Actions（按给定顺序执行，至少一个）:
  deploy LOCAL REMOTE [--exclude PATTERN]...
                         rsync LOCAL 目录到 REMOTE。可多次。

  restart-server         重启 quant-web-api（uvicorn :8001）
  restart-celery         重启 celery-worker + celery-beat
  restart-web            reload httpd（不断连接，不影响 safebox）
  restart-pgsql          重启 PostgreSQL（共享，短暂断连）
  restart-redis          重启 redis6（共享，短暂断连）

  clear-pgsql            DROP+CREATE quant 库（destructive，停 quant 服务，不碰 safebox 库）
                         跑 init-seed.sql 重建 sync_config 种子后重启服务
  clear-redis            只清 db2(VALKEY)+db3(CELERY)（destructive，绝不 FLUSHALL，不碰 safebox db0）
  fix-venv               sed venv/bin/* shebang -> PROJECT_PATH（迁路径后修 203/EXEC，保留 .so）
  install-services       lint 占位符 + cp service/polkit -> /etc + daemon-reload（不 enable）
  enable-services        enable 核心 4 服务（web-api/celery-worker/beat/feishu-bot@*，strategy 按需不 enable）
  migrate                alembic upgrade head（schema 版本迁移，主用，quant 用户跑）
  pip-install            pip install -r requirements.txt（装新依赖，如 croniter，deploy 后跑）
  init-schema            跑 init-schema.sql 集中建表（备用，postgres 用户跑）
  init-seed              跑 init-seed.sql 初始化 sync_config 种子（幂等，不覆盖已有）

Examples:
  # 首次部署（服务器 IP 用默认 quant.snailtrail.cc，无需 --server）
  $0 deploy ./server /data/websites/snailtrail.cc/quant/server --exclude .env migrate init-seed restart-server restart-celery

  # 日常更新后端
  $0 deploy ./server /data/websites/snailtrail.cc/quant/server --exclude .env restart-server restart-celery

  # 清 quant 库重建
  $0 clear-pgsql restart-server restart-celery

Notes:
  - clear-pgsql/clear-redis 是 destructive，clear-redis 绝不用 FLUSHALL
  - restart-web 用 reload 不影响 safebox
EOF
}

#-----------------------------------------------------------------------------------------------------------------------
# 解析全局选项
#-----------------------------------------------------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --server) SERVER="$2"; shift 2 ;;
        --user) USER="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        # F-15 显式覆盖（2026-08-18）：所有者明确指令时用——命令行参数形式可穿过 sudo
        # 单命令规则（env_reset 会吃环境变量）；效果等同 FORCE_DEPLOY=1
        --force) FORCE_DEPLOY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        --*) echo "❌ 未知全局选项: $1" >&2; usage; exit 1 ;;
        *) break ;;
    esac
done

[[ -z "$SERVER" ]] && { echo "❌ --server 必填" >&2; usage; exit 1; }
SSH_TARGET="${USER:+$USER@}$SERVER"

#-----------------------------------------------------------------------------------------------------------------------
# 解析动作
#-----------------------------------------------------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        # --force 也允许出现在动作序列任意位置（deploy-server.sh 把透传参数排在动作之后）
        --force) FORCE_DEPLOY=1; shift ;;
        deploy)
            HAS_ACTION=true
            [[ $# -lt 3 ]] && { echo "❌ deploy 需要 LOCAL REMOTE" >&2; exit 1; }
            local_path="$2"; remote_path="$3"; shift 3
            deploy_excludes=()
            while [[ $# -gt 0 ]]; do
                case "$1" in
                    --exclude) [[ $# -lt 2 ]] && { echo "❌ --exclude 需要模式" >&2; exit 1; }; deploy_excludes+=("$2"); shift 2 ;;
                    *) break ;;
                esac
            done
            excludes_csv=$(IFS=,; echo "${deploy_excludes[*]}")
            DEPLOY_TASKS+=("$local_path|$remote_path|$excludes_csv")
            ;;
        restart-server) HAS_ACTION=true; RESTART_SERVER=true; shift ;;
        restart-celery) HAS_ACTION=true; RESTART_CELERY=true; shift ;;
        restart-hub) HAS_ACTION=true; RESTART_HUB=true; shift ;;
        restart-web) HAS_ACTION=true; RESTART_WEB=true; shift ;;
        restart-feishu) HAS_ACTION=true; RESTART_FEISHU=true; shift ;;
        restart-pgsql) HAS_ACTION=true; RESTART_PGSQL=true; shift ;;
        restart-redis) HAS_ACTION=true; RESTART_REDIS=true; shift ;;
        fix-venv) HAS_ACTION=true; FIX_VENV=true; shift ;;
        install-services) HAS_ACTION=true; INSTALL_SERVICES=true; shift ;;
        enable-services) HAS_ACTION=true; ENABLE_SERVICES=true; shift ;;
        clear-pgsql) HAS_ACTION=true; CLEAR_PGSQL=true; shift ;;
        clear-redis) HAS_ACTION=true; CLEAR_REDIS=true; shift ;;
        init-seed) HAS_ACTION=true; INIT_SEED=true; shift ;;
        init-schema) HAS_ACTION=true; INIT_SCHEMA=true; shift ;;
        migrate) HAS_ACTION=true; MIGRATE=true; shift ;;
        pip-install) HAS_ACTION=true; PIP_INSTALL=true; shift ;;
        *) echo "❌ 未知动作: $1" >&2; usage; exit 1 ;;
    esac
done

$HAS_ACTION || { echo "❌ 至少一个动作" >&2; usage; exit 1; }

# 校验本地 deploy 路径（文件或目录均可）
for task in "${DEPLOY_TASKS[@]}"; do
    IFS='|' read -r local_path _ _ <<< "$task"
    [[ ! -e "$local_path" ]] && { echo "❌ 本地路径不存在: $local_path" >&2; exit 1; }
done

#-----------------------------------------------------------------------------------------------------------------------
# 依赖检查
#-----------------------------------------------------------------------------------------------------------------------
local_missing=()
command -v ssh >/dev/null 2>&1 || local_missing+=("ssh")
[[ ${#DEPLOY_TASKS[@]} -gt 0 ]] && ! command -v rsync >/dev/null 2>&1 && local_missing+=("rsync")
[[ ${#local_missing[@]} -gt 0 ]] && { echo "❌ 本地缺依赖: ${local_missing[*]}" >&2; exit 1; }

if ! $DRY_RUN; then
    remote_tools=("bash" "sudo" "systemctl")
    if $RESTART_PGSQL || $CLEAR_PGSQL || $INIT_SEED || $INIT_SCHEMA; then remote_tools+=("psql"); fi
    if $RESTART_REDIS || $CLEAR_REDIS; then remote_tools+=("$REDIS_CLI"); fi

    # 简单远程依赖检查：ssh 跑 command -v 列表，缺失的工具名 echo 出来
    # （不用 bash -c '...' 嵌套，避免 [ ] false 时退出码 1 误报）
    missing_check=""
    for cmd in "${remote_tools[@]}"; do
        missing_check+="command -v $cmd >/dev/null 2>&1 || echo $cmd; "
    done
    if ! missing_output=$(ssh $SSH_OPTS "$SSH_TARGET" "$missing_check" 2>&1); then
        echo "❌ ssh 连接失败: $missing_output" >&2; exit 1
    fi
    if [[ -n "$missing_output" ]]; then
        echo "❌ 远程缺依赖: $missing_output" >&2; exit 1
    fi
fi

#-----------------------------------------------------------------------------------------------------------------------
# Dry-run
#-----------------------------------------------------------------------------------------------------------------------
if $DRY_RUN; then
    echo "🔍 Dry-run on $SSH_TARGET:"
    for task in "${DEPLOY_TASKS[@]}"; do
        IFS='|' read -r local_path remote_path excludes_csv <<< "$task"
        echo "🔍  deploy $local_path -> $remote_path  excludes: ${excludes_csv//,/ }"
    done
    $MIGRATE && echo "🔍  alembic upgrade head（schema 版本迁移）"
    $PIP_INSTALL && echo "🔍  pip install -r requirements.txt（装新依赖）"
    $INIT_SCHEMA && echo "🔍  跑 init-schema.sql 集中建表（owner=quant）"
    $INIT_SEED && echo "🔍  跑 init-seed.sql 初始化 sync_config 种子"
    $RESTART_SERVER && echo "🔍  restart $WEB_API_SERVICE"
    $RESTART_CELERY && echo "🔍  restart $CELERY_WORKER_SERVICE + $CELERY_BEAT_SERVICE"
    $RESTART_FEISHU && echo "🔍  restart feishu bots (自动检测)"
    $RESTART_WEB && echo "🔍  reload $WEB_SERVICE (不影响 safebox)"
    $RESTART_PGSQL && echo "🔍  restart $PGSQL_SERVICE (共享,短暂断连)"
    $RESTART_REDIS && echo "🔍  restart $REDIS_SERVICE (共享,短暂断连)"
    $CLEAR_PGSQL && echo "🔍🔥 clear-pgsql: DROP+CREATE quant 库 + init-seed (不碰 safebox)"
    $CLEAR_REDIS && echo "🔍🔥 clear-redis: FLUSHDB db$VALKEY_DB + db$CELERY_DB (绝不 FLUSHALL)"
    $FIX_VENV && echo "🔍  fix-venv: sed venv/bin/* shebang -> $PROJECT_PATH"
    $INSTALL_SERVICES && echo "🔍  install-services: lint + cp service/polkit + daemon-reload"
    $ENABLE_SERVICES && echo "🔍  enable-services: 核心 4 服务 enable"
    echo "🔍 Dry-run done."
    exit 0
fi

#-----------------------------------------------------------------------------------------------------------------------
# SE3 部署闸门（F-15/F-53/F-54）：交易时段+实盘任务运行 → 拒绝；代码无变化 → 跳过重启
#-----------------------------------------------------------------------------------------------------------------------
trading_guard() {
    if [[ "${FORCE_DEPLOY:-0}" == "1" ]]; then
        echo "⚠️ FORCE_DEPLOY=1，跳过交易时段闸门（自担风险）"
        return 0
    fi
    local state
    state=$(ssh $SSH_OPTS "$SSH_TARGET" "
        active=\$(systemctl list-units 'quant-live-task@*' 'quant-strategy@*' 'quant-md-hub@*' --state=active --no-legend 2>/dev/null | wc -l)
        day=\$(date +%u); hm=\$(date +%H%M); in_sess=0
        if [ \$day -le 5 ] && { [ \$hm -ge 0915 ] && [ \$hm -le 1135 ] || [ \$hm -ge 1255 ] && [ \$hm -le 1505 ]; }; then in_sess=1; fi
        echo \$in_sess \$active")
    read -r in_sess active <<< "$state"
    if [[ "$in_sess" == "1" && "${active:-0}" -gt 0 ]]; then
        echo "❌ 交易时段且有 ${active} 个实盘任务运行中，拒绝部署（F-15）。FORCE_DEPLOY=1 可强行覆盖" >&2
        exit 1
    fi
    echo "✅ 部署闸门通过（非交易时段或无运行中实盘任务）"
}

remote_code_hash() {
    # SE3 指纹四切片（2026-08-25 止血）：src/*.py + systemd 单元 + requirements + migrations
    # ——原只指纹 *.py，单元/依赖/迁移变更不触发重启（缺口实锤）。michael 无权 cd 进 750
    # quant 目录（2026-08-17 踩坑同款），统一 sudo find 免 cd；取不到指纹（空）时上层按
    # CODE_CHANGED=1 处理（宁可多重启不漏重启）
    ssh $SSH_OPTS "$SSH_TARGET" "
        { sudo find '$PROJECT_PATH/src' -name '*.py' -not -path '*__pycache__*' -exec md5sum {} + 2>/dev/null
          sudo find '$PROJECT_PATH/scripts/systemd' -type f -exec md5sum {} + 2>/dev/null
          sudo md5sum '$PROJECT_PATH/requirements.txt' 2>/dev/null
          sudo find '$PROJECT_PATH/migrations/versions' -type f -exec md5sum {} + 2>/dev/null
        } | sort | md5sum"
}

verify_imports() {
    echo "ℹ️ 部署物导入冒烟（rsync 后/重启前——2026-08-25 实锤：exclude 未锚定把
  runtime/ 整包排除出部署，服务重启后才 ModuleNotFoundError 暴露）..."
    # michael 无权 cd 进 750 quant 目录（2026-08-17 踩坑同款）——整块 sudo bash -s 以
    # root 跑（踩坑记录解法），python 以 quant 跑保环境一致，带 .env（部分模块 import 期读环境变量）
    ssh $SSH_OPTS "$SSH_TARGET" "sudo bash -s" <<REMOTE_SCRIPT
set -euo pipefail
cd $PROJECT_PATH
set -a; source .env; set +a
for m in src.md_hub.main src.strategy_runner.main src.web_api.main src.scheduler.app src.strategy_framework.runtime.loop src.strategy_framework.md_api_guard; do
    sudo -u quant venv/bin/python -c "import \$m" || { echo "❌ 导入失败: \$m——中止重启（服务未动，线上仍跑旧代码）" >&2; exit 1; }
done
echo "✅ 导入冒烟通过（全部入口模块可导入）"
REMOTE_SCRIPT
}

# 代码变更时让实盘任务吃到新代码（闸门已保证非交易时段才走到这）
restart_live_tasks() {
    ssh $SSH_OPTS "$SSH_TARGET" "
        for u in \$(systemctl list-units 'quant-live-task@*' 'quant-strategy@*' --state=active --no-legend 2>/dev/null | awk '{print \$1}'); do
            echo "  restart \$u"; sudo systemctl restart "\$u"
        done"
}

#-----------------------------------------------------------------------------------------------------------------------
# 核心函数
#-----------------------------------------------------------------------------------------------------------------------
rsync_deploy() {
    local local_path="$1" remote_path="$2" excludes_csv="$3"
    local exclude_opts=""
    if [[ -n "$excludes_csv" ]]; then
        IFS=',' read -ra excludes <<< "$excludes_csv"
        for pat in "${excludes[@]}"; do exclude_opts="$exclude_opts --exclude=$pat"; done
    fi
    echo "ℹ️ Deploy $local_path -> $SSH_TARGET:$remote_path..."
    local temp_dir="/tmp/quant-deploy-$$-$(date +%s)-$RANDOM"
    if [[ -d "$local_path" ]]; then
        # 目录：rsync 内容到 temp_dir，--delete 同步
        # shellcheck disable=SC2086
        rsync -avz --delete -e "ssh $SSH_OPTS" $exclude_opts "$local_path/" "$SSH_TARGET:$temp_dir/"
        # shellcheck disable=SC2086
        ssh $SSH_OPTS "$SSH_TARGET" "sudo rsync -a --delete $exclude_opts $temp_dir/ $remote_path/ && sudo chown -R quant:quant $remote_path && rm -rf $temp_dir"
    else
        # 单文件：rsync 直接传成 temp_dir（文件），cp 到 remote_path（不用 --delete，避免误删目标目录其他文件）
        # shellcheck disable=SC2086
        rsync -avz -e "ssh $SSH_OPTS" $exclude_opts "$local_path" "$SSH_TARGET:$temp_dir"
        ssh $SSH_OPTS "$SSH_TARGET" "sudo cp $temp_dir $remote_path && sudo chown quant:quant $remote_path && rm -f $temp_dir"
    fi
    echo "✅ Deployment done."
}

init_seed() {
    echo "ℹ️ 跑 init-seed.sql 初始化 sync_config 种子..."
    # init-seed.sql 已随 deploy 到服务器 PROJECT_PATH/scripts/
    ssh $SSH_OPTS "$SSH_TARGET" "sudo -u quant bash -c 'cd $PROJECT_PATH && psql -d $PG_DB -f scripts/init-seed.sql'" || \
        echo "⚠️ init-seed 失败（scripts/init-seed.sql 是否已部署？）"
    echo "✅ init-seed done."
}

init_schema() {
    echo "ℹ️ 跑 init-schema.sql 集中建表（owner=quant）..."
    # 用 postgres superuser 跑（ALTER OWNER 需要 superuser）
    ssh $SSH_OPTS "$SSH_TARGET" "sudo -u postgres psql -d $PG_DB -f $SCHEMA_SQL" || \
        echo "⚠️ init-schema 失败（scripts/init-schema.sql 是否已部署？）"
    echo "✅ init-schema done."
}

migrate() {
    echo "ℹ️ alembic upgrade head（schema 版本迁移，用 quant 用户跑，owner 自动 quant）..."
    # 2026-08-25 止血：原 `|| echo` 吞错续行——代码已推+schema 未到位+服务照常重启 =
    # 新代码跑旧 schema 的静默漂移（最危险中间态）。改：失败中止（此时服务尚未重启，
    # 磁盘新代码未被任何进程加载，属可恢复态：修好后重跑部署即可，rsync 幂等）
    if ! ssh $SSH_OPTS "$SSH_TARGET" "sudo -u quant bash -c 'cd $PROJECT_PATH && venv/bin/alembic upgrade head'"; then
        echo "❌ migrate 失败——中止部署链（服务未重启、新代码未被加载；查 venv/bin/alembic 与 alembic.ini）" >&2
        return 1
    fi
    echo "✅ migrate done."
}

pip_install() {
    echo "ℹ️ pip install -r requirements.txt（装新依赖，清华镜像）..."
    ssh $SSH_OPTS "$SSH_TARGET" "sudo -u quant bash -c 'cd $PROJECT_PATH && venv/bin/pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt'"
    echo "✅ pip install done."
}

clear_pgsql() {
    echo "🔥 DESTRUCTIVE: DROP+CREATE quant 库（不碰 safebox）。quant 服务将临时停止。"
    ssh $SSH_OPTS "$SSH_TARGET" bash <<REMOTE_SCRIPT
set -euo pipefail
echo "ℹ️ 停止 quant 服务..."
sudo systemctl stop $WEB_API_SERVICE $CELERY_WORKER_SERVICE $CELERY_BEAT_SERVICE || true

echo "ℹ️ 安全检查: 确认 safebox 库存在（防止误操作）..."
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='safebox'" | grep -q 1 || echo "⚠️ safebox 库不存在（可能未部署 safebox，继续）"

echo "ℹ️ DROP+CREATE quant 库..."
sudo -u postgres psql -v ON_ERROR_STOP=1 -c 'DROP DATABASE IF EXISTS $PG_DB WITH (FORCE);'
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE $PG_DB OWNER $PG_USER;"
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "GRANT ALL ON DATABASE $PG_DB TO $PG_USER;"

echo "ℹ️ 启动 quant-web-api（startup 钩子建表 + 默认 admin）..."
sudo systemctl start $WEB_API_SERVICE
sleep 3

echo "ℹ️ 跑 init-seed.sql..."
sudo -u quant bash -c 'cd $PROJECT_PATH && psql -d $PG_DB -f scripts/init-seed.sql'

echo "ℹ️ 启动 celery..."
sudo systemctl start $CELERY_WORKER_SERVICE $CELERY_BEAT_SERVICE

echo "✅ quant 库已重建: \$(sudo systemctl is-active $WEB_API_SERVICE)"
REMOTE_SCRIPT
    echo "✅ clear-pgsql done."
}

clear_redis() {
    echo "🔥 DESTRUCTIVE: 清 Redis db$VALKEY_DB(VALKEY) + db$CELERY_DB(broker) + db$CELERY_RESULT_DB(result)（绝不 FLUSHALL，不碰 safebox db0）..."
    ssh $SSH_OPTS "$SSH_TARGET" "sudo $REDIS_CLI -n $VALKEY_DB FLUSHDB && sudo $REDIS_CLI -n $CELERY_DB FLUSHDB && sudo $REDIS_CLI -n $CELERY_RESULT_DB FLUSHDB"
    echo "✅ Redis db$VALKEY_DB + db$CELERY_DB + db$CELERY_RESULT_DB 已清（safebox db0 未动）。"
}

restart_server() {
    echo "ℹ️ Restart $WEB_API_SERVICE..."
    # 2026-08-25 止血：restart 类命令失败不杀链（健康判定交给 _stabilize）——链断会跳过
    # 后续 restart_live_tasks，实盘任务滞留旧代码（当日实锤：hub 段断链，任务 8 盲跑 15min）
    ssh $SSH_OPTS "$SSH_TARGET" "sudo systemctl restart $WEB_API_SERVICE" || \
        echo "⚠️ $WEB_API_SERVICE restart 命令失败——链条继续，由下方稳定检查判定健康"
    # 1. is-active 稳定校验：systemd 单元本身必须 active。
    #    防僵尸进程占 8001（非 systemd 的旧 uvicorn）→ 新单元 bind 失败 crash-loop，
    #    但下方 health 打到僵尸进程会假成功。is-active 在 crash-loop 时≠active，能抓到。
    if ! _stabilize "$WEB_API_SERVICE" 15; then
        echo "❌ $WEB_API_SERVICE 未稳定 active（crash-loop 或 8001 被非 systemd 进程占用）"
        echo "   诊断：ssh 到服务器跑 'sudo ss -ltnp | grep 8001'（看占端口 PID+启动时间）"
        echo "         'sudo systemctl status $WEB_API_SERVICE'（看单元是否 failed/restart-loop）"
        echo "   修复：sudo systemctl stop $WEB_API_SERVICE && sudo kill <僵尸PID> && sudo systemctl start $WEB_API_SERVICE"
        return 1
    fi
    echo "  ✅ $WEB_API_SERVICE active (稳定)"
    # 2. health 检查（此时打到的是 systemd 起的新进程）
    local ok=0
    for i in $(seq 1 15); do
        if ssh $SSH_OPTS "$SSH_TARGET" 'curl -sf http://127.0.0.1:8001/health' >/dev/null 2>&1; then
            echo "✅ health ok（第 ${i} 次尝试，约 $((i*2))s）"
            ok=1
            break
        fi
        sleep 2
    done
    if [ $ok -eq 0 ]; then
        echo "⚠️ health check 失败（30s 未就绪，看 journalctl -u $WEB_API_SERVICE -n 50）"
        return 1
    fi
    echo "✅ restart-server done."
}

# 服务就绪稳定检查（检测 crash-loop）：轮询 is-active，连续 running 才算稳
_stabilize() {
    local svc="$1" tries="${2:-4}"
    local prev=""
    for i in $(seq 1 $tries); do
        sleep 2
        prev=$(ssh $SSH_OPTS "$SSH_TARGET" "sudo systemctl is-active $svc 2>/dev/null" || true)
        if [[ "$prev" == "active" ]]; then
            # 再确认一次（crash-loop 会在 activating/activating 间跳）
            sleep 2
            local again=$(ssh $SSH_OPTS "$SSH_TARGET" "sudo systemctl is-active $svc 2>/dev/null" || true)
            [[ "$again" == "active" ]] && return 0
        fi
    done
    return 1
}

restart_celery() {
    echo "ℹ️ Restart $CELERY_WORKER_SERVICE + $CELERY_BEAT_SERVICE + $CELERY_RISK_SERVICE..."
    # 2026-08-25 止血：同 restart_server——restart 命令失败不杀链
    ssh $SSH_OPTS "$SSH_TARGET" "sudo systemctl restart $CELERY_WORKER_SERVICE $CELERY_BEAT_SERVICE $CELERY_RISK_SERVICE" || \
        echo "⚠️ celery restart 命令失败——链条继续，由下方稳定检查判定健康"
    # 稳定检查（crash-loop 检测）
    for svc in $CELERY_WORKER_SERVICE $CELERY_BEAT_SERVICE $CELERY_RISK_SERVICE; do
        if _stabilize "$svc"; then
            echo "  ✅ $svc active (稳定)"
        else
            echo "  ⚠️ $svc 未稳定（可能 crash-loop，看 journalctl -u $svc -n 30）"
        fi
    done
    echo "✅ restart-celery done."
}


# 飞书 bot 实例清单的真相源：feishu_config DB（enabled 的 id）。
# 不用"正在运行的实例"——那会把幽灵实例（如 @multi-user 误启）每次部署转正（2026-08-17 踩坑）。
# DB 不可达时 fallback 到 active 列表（保守：只重启不新增）。
feishu_bot_units() {
    local ids
    ids=$(ssh $SSH_OPTS "$SSH_TARGET" "sudo -u quant psql -d $PG_DB -tAc 'SELECT id FROM feishu_config WHERE enabled' 2>/dev/null" | tr '\n' ' ')
    if [[ -n "${ids// /}" ]]; then
        local units=""
        for id in $ids; do
            units="$units quant-feishu-bot@${id}.service"
        done
        echo "$units"
    else
        echo "__FALLBACK__"
    fi
}

restart_hub() {
    # 首次部署单元尚未安装（install-services 在链尾）——容忍跳过，不中断部署链
    if ! ssh $SSH_OPTS "$SSH_TARGET" "systemctl cat '$MD_HUB_SERVICE' >/dev/null 2>&1"; then
        echo "⏭️  skip restart-hub（单元未安装，稍后 install-services 装）"
        return 0
    fi
    echo "ℹ️ Restart $MD_HUB_SERVICE..."
    # 2026-08-25 止血：同 restart_server——restart 命令失败不杀链（当日 hub 段断链实锤）
    ssh $SSH_OPTS "$SSH_TARGET" "sudo systemctl restart $MD_HUB_SERVICE" || \
        echo "⚠️ $MD_HUB_SERVICE restart 命令失败——链条继续，由下方稳定检查判定健康"
    # hub 加载 XTP 全市场合约慢（默认 4 次 ~16s 必误报，2026-08-23 两次部署实测）--加长到 ~60s
    if _stabilize "$MD_HUB_SERVICE" 15; then
        echo "  ✅ $MD_HUB_SERVICE active (稳定)"
    else
        echo "  ⚠️ $MD_HUB_SERVICE 未稳定（journalctl -u $MD_HUB_SERVICE -n 30）"
    fi
}

restart_feishu() {
    echo "ℹ️ Restart feishu bots..."
    local bots
    bots=$(feishu_bot_units)
    if [[ "$bots" == "__FALLBACK__" ]]; then
        echo "  ⚠️ feishu_config 不可读，fallback 到 active 实例（不新增）"
        bots=$(ssh $SSH_OPTS "$SSH_TARGET" "systemctl list-units 'quant-feishu-bot@*' --no-legend --type=service --state=active,running 2>/dev/null | awk '{print \$1}'")
    fi
    if [[ -z "$bots" ]]; then
        echo "⚠️ 无 feishu bot（skipped）"
        return
    fi
    for bot in $bots; do
        echo "  restart $bot"
        ssh $SSH_OPTS "$SSH_TARGET" "sudo systemctl restart $bot" || { echo "  ⚠️ $bot 不存在或启动失败"; continue; }
        if _stabilize "$bot"; then
            echo "  ✅ $bot active (稳定)"
        else
            echo "  ⚠️ $bot 未稳定（看 journalctl -u $bot -n 30）"
        fi
    done
    echo "✅ restart-feishu done."
}

restart_web() {
    echo "ℹ️ Reload $WEB_SERVICE（不断连接，不影响 safebox）..."
    ssh $SSH_OPTS "$SSH_TARGET" "sudo systemctl reload $WEB_SERVICE && echo '✅ ' \$(sudo systemctl is-active $WEB_SERVICE)"
    echo "✅ reload-web done."
}

restart_pgsql() {
    echo "ℹ️ Restart $PGSQL_SERVICE（共享，quant 会短暂断连）..."
    ssh $SSH_OPTS "$SSH_TARGET" "sudo systemctl restart $PGSQL_SERVICE && echo '✅ ' \$(sudo systemctl is-active $PGSQL_SERVICE)"
}

restart_redis() {
    echo "ℹ️ Restart $REDIS_SERVICE（共享，quant 心跳锁/Celery 短暂受影响）..."
    ssh $SSH_OPTS "$SSH_TARGET" "sudo systemctl restart $REDIS_SERVICE && echo '✅ ' \$(sudo systemctl is-active $REDIS_SERVICE)"
}

#-----------------------------------------------------------------------------------------------------------------------
# venv shebang 修复（迁路径后 venv/bin/* 指向旧路径 -> 203/EXEC）
#-----------------------------------------------------------------------------------------------------------------------
fix_venv() {
    echo "ℹ️ Fix venv shebangs -> $PROJECT_PATH/venv/bin/..."
    # sudo（michael 无权读 quant 的 750 venv）；跳过 activate*（无 venv shebang）；
    # sed '1s' 只改首行匹配 #!...venv/bin/ 的脚本，无匹配则 no-op（幂等）
    ssh $SSH_OPTS "$SSH_TARGET" "sudo find $PROJECT_PATH/venv/bin -maxdepth 1 -type f ! -name 'activate*' -exec sed -i '1s|^#!.*venv/bin/|#!$PROJECT_PATH/venv/bin/|' {} + && echo '✅ venv shebang 已同步到 $PROJECT_PATH'"
}

#-----------------------------------------------------------------------------------------------------------------------
# 安装 systemd 服务 + polkit（lint 占位符 -> cp -> daemon-reload）
#-----------------------------------------------------------------------------------------------------------------------
install_services() {
    echo "ℹ️ Install systemd services + polkit rules..."
    # 整块用 root 跑：PROJECT_PATH 树是 750 quant:quant，michael 无法穿越（test -d/glob/cp 全废）
    ssh $SSH_OPTS "$SSH_TARGET" "sudo bash -s" <<REMOTE
set -euo pipefail
SRC='$SYSTEMD_SRC'
[[ -d "\$SRC" ]] || { echo "❌ 源码 systemd 目录不存在: \$SRC（先 deploy）"; exit 1; }

echo "  lint: 检查 service 文件无占位符（__X__）/User=%i..."
for f in \$SRC/*.service; do
    if grep -qE '__[A-Z_]+__|User=%i' "\$f"; then
        echo "❌ \$f 有占位符或 User=%i（需写实路径/User=$QUANT_USER）"
        grep -nE '__[A-Z_]+__|User=%i' "\$f"
        exit 1
    fi
done
echo "  ✅ lint 通过"

echo "  cp service 模板 -> $SYSTEMD_DST/"
sudo cp \$SRC/quant-*.service $SYSTEMD_DST/

echo "  cp polkit rules -> $POLKIT_DST/"
sudo cp \$SRC/*.rules $POLKIT_DST/ 2>/dev/null || echo "  ⚠️ 无 .rules 文件"

sudo systemctl daemon-reload
# polkit rules.d 在 al8 上不热加载，必须 restart polkit 才认新规则（2026-08-17 实测踩坑）
sudo systemctl restart polkit
echo "  当前 quant-* 单元状态快照（人工扫一眼，防幽灵实例）："
systemctl list-unit-files 'quant-*' --no-legend | grep -v '^$' || true
echo "✅ install-services done（未 enable，用 enable-services 或手动 enable）"
REMOTE
}

#-----------------------------------------------------------------------------------------------------------------------
# enable 核心服务（web-api/celery-worker/celery-beat/feishu-bot@*），strategy 不 enable（按需）
#-----------------------------------------------------------------------------------------------------------------------
enable_services() {
    echo "ℹ️ Enable core services（strategy@ 按需，不 enable）..."
    # 飞书实例来自 feishu_config DB（真相源），不收集 active 实例（防幽灵转正，2026-08-17）
    local feishu
    feishu=$(feishu_bot_units)
    if [[ "$feishu" == "__FALLBACK__" ]]; then
        echo "  ⚠️ feishu_config 不可读，飞书不 enable（避免转正幽灵实例）"
        feishu=""
    fi
    local cores="$WEB_API_SERVICE $CELERY_WORKER_SERVICE $CELERY_BEAT_SERVICE $CELERY_RISK_SERVICE $MD_HUB_SERVICE $feishu"
    ssh $SSH_OPTS "$SSH_TARGET" "sudo systemctl enable $cores 2>&1 | grep -v 'Created symlink\|^$' || true; echo '✅ enabled: $cores'"
}

#-----------------------------------------------------------------------------------------------------------------------
# 执行（按顺序）
#-----------------------------------------------------------------------------------------------------------------------
trading_guard
PRE_CODE_HASH=$(remote_code_hash)

for task in "${DEPLOY_TASKS[@]}"; do
    IFS='|' read -r local_path remote_path excludes_csv <<< "$task"
    rsync_deploy "$local_path" "$remote_path" "$excludes_csv"
done

# SE3 选择性重启：代码指纹没变就跳过服务重启（F-53：不"动辄停服务"）
POST_CODE_HASH=$(remote_code_hash)
CODE_CHANGED=1
if [[ -n "$PRE_CODE_HASH" && "$PRE_CODE_HASH" == "$POST_CODE_HASH" ]]; then
    CODE_CHANGED=0
    echo "ℹ️ 代码指纹无变化，跳过服务重启（unit/migrate 动作仍执行）"
fi

# 部署物导入冒烟门（2026-08-25 踩坑后新增）：指纹判断之后、任何重启/migrate 之前
verify_imports

$MIGRATE && migrate
$PIP_INSTALL && pip_install
$INIT_SCHEMA && init_schema
$INIT_SEED && init_seed
$CLEAR_PGSQL && clear_pgsql
$CLEAR_REDIS && clear_redis
$INSTALL_SERVICES && install_services
$FIX_VENV && fix_venv
if $RESTART_WEB; then
    if [[ $CODE_CHANGED -eq 1 || "${FORCE_RESTART:-0}" == "1" || "${FORCE_DEPLOY:-0}" == "1" ]]; then restart_web; else echo "⏭️  skip restart-web（代码无变化）"; fi
fi
$RESTART_REDIS && restart_redis
$RESTART_PGSQL && restart_pgsql
if $RESTART_CELERY; then
    if [[ $CODE_CHANGED -eq 1 || "${FORCE_RESTART:-0}" == "1" || "${FORCE_DEPLOY:-0}" == "1" ]]; then restart_celery; else echo "⏭️  skip restart-celery（代码无变化）"; fi
fi
if $RESTART_FEISHU; then
    if [[ $CODE_CHANGED -eq 1 || "${FORCE_RESTART:-0}" == "1" || "${FORCE_DEPLOY:-0}" == "1" ]]; then restart_feishu; else echo "⏭️  skip restart-feishu（代码无变化）"; fi
fi
if $RESTART_HUB; then
    if [[ $CODE_CHANGED -eq 1 || "${FORCE_RESTART:-0}" == "1" || "${FORCE_DEPLOY:-0}" == "1" ]]; then restart_hub; else echo "⏭️  skip restart-hub（代码无变化）"; fi
fi
if $RESTART_SERVER; then
    if [[ $CODE_CHANGED -eq 1 || "${FORCE_RESTART:-0}" == "1" || "${FORCE_DEPLOY:-0}" == "1" ]]; then restart_server; else echo "⏭️  skip restart-server（代码无变化）"; fi
fi
$ENABLE_SERVICES && enable_services

# SE3：代码变更时让实盘任务吃到新代码（闸门已确保非交易时段/无任务冲突）
if [[ $CODE_CHANGED -eq 1 ]] && { $RESTART_SERVER || $RESTART_CELERY; }; then
    echo "ℹ️ 代码已变更，重启运行中的实盘任务以加载新代码..."
    restart_live_tasks
fi

echo "✅ All operations completed."
