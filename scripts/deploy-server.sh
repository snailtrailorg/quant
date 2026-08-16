#!/bin/bash
# 部署后端 server/ + 重启 web-api + celery + 飞书。
# Usage: ./scripts/deploy-server.sh [--dry-run] [init-seed] [clear-pgsql] [clear-redis] [install-services] [fix-venv] [enable-services]
#
# 整个 server/ 目录 rsync 到远程（排除 .env/venv），含 src/ + scripts/init-seed.sql + systemd/ + requirements.txt。
# 迁路径后加 fix-venv（修 venv shebang）；首次/改 service 加 install-services。
# 开发机部署工具（deploy-*.sh/quant-deploy.sh）在项目根 scripts/，不在此目录，不会传服务器。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL="$(cd "$SCRIPT_DIR/../server" && pwd)"
REMOTE="${REMOTE:-/data/websites/snailtrail.cc/quant/server}"

EXCLUDES=(
    --exclude .env
    --exclude venv/
    --exclude __pycache__/
    --exclude '*.pyc'
    --exclude .pytest_cache/
    --exclude static/avatars/   # 用户上传的头像（运行时数据，rsync --delete 不删）
)

# 路径/SERVER 默认值在 quant-deploy.sh 单一配置源，此处不重复
# 顺序：deploy -> fix-venv -> pip-install -> migrate -> restart-services
sudo -u michael /home/michael/.local/bin/quant-deploy.sh \
    deploy "$LOCAL" "$REMOTE" "${EXCLUDES[@]}" \
    fix-venv \
    pip-install \
    migrate \
    restart-server \
    restart-celery \
    restart-feishu \
    "$@"
