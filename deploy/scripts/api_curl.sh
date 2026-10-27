#!/bin/bash
# 安全页/API 调试：登录换 token（缓存 30 分钟）后发起请求。
# 用法：api_curl.sh <path> [curl 额外参数...]   例：api_curl.sh /api/security/snapshot
# 凭据来源：环境变量 QUANT_API_USER/QUANT_API_PASS（由调用方从 ~/.config/quant/prod_admin.env 注入）
set -uo pipefail
BASE=${QUANT_API_BASE:-http://127.0.0.1:8001}
CACHE=${TMPDIR:-/tmp}/quant_api_token_$(id -u)
PATH_=${1:?path required}
shift || true

_fetch_token() {
  curl -s -m 10 -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$QUANT_API_USER\",\"password\":\"$QUANT_API_PASS\"}" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("token") or d.get("access_token") or "")'
}

T=""
if [ -s "$CACHE" ] && [ $(( $(date +%s) - $(stat -c %Y "$CACHE") )) -lt 1800 ]; then
  T=$(cat "$CACHE")
fi
if [ -z "$T" ]; then
  T=$(_fetch_token)
  [ -z "$T" ] && { echo '{"error":"login failed"}' >&2; exit 2; }
  printf %s "$T" > "$CACHE"; chmod 600 "$CACHE"
fi

curl -s -m 30 -H "Authorization: Bearer $T" "$@" "$BASE$PATH_"
