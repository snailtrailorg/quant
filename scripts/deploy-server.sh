#!/bin/bash
# 部署后端 server/ + 重启 web-api + celery。
# Usage: ./scripts/deploy-server.sh [--dry-run] [init-seed] [clear-pgsql] [clear-redis]
#
# 整个 server/ 目录 rsync 到远程（排除 .env/venv），含 src/ + scripts/init-seed.sql + systemd/ + requirements.txt。
# 开发机部署工具（deploy-*.sh/quant-deploy.sh）在项目根 scripts/，不在此目录，不会传服务器。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL="$(cd "$SCRIPT_DIR/../server" && pwd)"
REMOTE="/data/websites/snailtrail.org/quant/server"

EXCLUDES=(
    --exclude .env
    --exclude venv/
    --exclude __pycache__/
    --exclude '*.pyc'
    --exclude .pytest_cache/
)

# 服务器 IP 在 quant-deploy.sh 的 SERVER 默认值，无需在此定义
# 顺序：deploy -> pip-install（装新依赖如 croniter）-> migrate（alembic schema）-> restart
sudo -u michael /home/michael/.local/bin/quant-deploy.sh \
    deploy "$LOCAL" "$REMOTE" "${EXCLUDES[@]}" \
    pip-install \
    migrate \
    restart-server \
    restart-celery \
    "$@"
