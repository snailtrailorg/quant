#!/bin/bash
# 构建前端 web/ + 部署 dist + reload httpd。
# Usage: ./scripts/deploy-web.sh [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "ℹ️ 构建前端..."
(cd "$SCRIPT_DIR/../web" && npm run build)

LOCAL="$(cd "$SCRIPT_DIR/../web/dist" && pwd)"
REMOTE="/data/websites/snailtrail.cc/quant/web"

# 服务器 IP 在 quant-deploy.sh 的 SERVER 默认值，无需在此定义
sudo -u michael /home/michael/.local/bin/quant-deploy.sh \
    deploy "$LOCAL" "$REMOTE" \
    restart-web \
    "$@"
