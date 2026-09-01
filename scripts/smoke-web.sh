#!/usr/bin/env bash
# smoke-web.sh —— Web API 冒烟门（B5，web 长尾第一档 2026-09-01）
#
# 登录 → 带 token 逐端点断言 **HTTP 200 + JSON 顶层形状**（只打状态/结构，
# 不打数据值——数据漂移不误红）。绿=可交付（八步法步 5 web 交付前跑）。
#
# 用法:  bash scripts/smoke-web.sh            # 本地 dev（http://127.0.0.1:8000）
#        BASE_URL=... bash scripts/smoke-web.sh
# 凭证:  缺省 admin/admin123（仅本地 dev——实盘模式 admin 密码随机生成，
#        本脚本对 prod 无效；SMOKE_USER/SMOKE_PASS 可覆写，如 staging）
# 自限:  登录 401 即中止不重试（login 限流 10/min/IP——连试只会烧配额）
set -uo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
SMOKE_USER="${SMOKE_USER:-admin}"
SMOKE_PASS="${SMOKE_PASS:-admin123}"
PASS=0; FAIL=0; FAILED=()

# ── 登录（一次性，401 即死）──
TMPD=$(mktemp -d /tmp/smoke-web.XXXXXX); trap 'rm -rf "$TMPD"' EXIT   # 盲审 B-P2:固定名并发互踩/残留
LOGIN=$(curl -s --max-time 10 -o "$TMPD/login.json" -w '%{http_code}' \
  -X POST "$BASE_URL/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$SMOKE_USER\",\"password\":\"$SMOKE_PASS\"}")
if [ "$LOGIN" != "200" ]; then
  echo "✗ 登录失败 HTTP $LOGIN（dev 缺省 admin/admin123；staging 用 SMOKE_USER/SMOKE_PASS）——中止不重试"
  exit 2   # 与端点红(1)区分：CI 可判"环境/凭证问题"vs"端点问题"
fi
TOKEN=$(python3 -c "import json;print(json.load(open('$TMPD/login.json')).get('token',''))" 2>/dev/null)
[ -n "$TOKEN" ] || { echo "✗ 登录响应无 token 字段"; exit 2; }
echo "✓ 登录 OK（$SMOKE_USER）"

# check <名> <路径> <顶层形状断言（python 表达式，d=解析后 JSON）>
check() {
  local name="$1" path="$2" shape="${3:-d is not None}"
  local code
  code=$(curl -s --max-time 10 -o "$TMPD/out.json" -w '%{http_code}' \
    -H "Authorization: Bearer $TOKEN" "$BASE_URL$path")
  if [ "$code" != "200" ]; then
    echo "✗ $name  HTTP $code  $path"; FAIL=$((FAIL+1)); FAILED+=("$name"); return
  fi
  if ! python3 -c "
import json, sys
try: d = json.load(open('$TMPD/out.json'))
except Exception: sys.exit(1)
sys.exit(0 if ($shape) else 1)" 2>/dev/null; then
    echo "✗ $name  形状不符  $path"; FAIL=$((FAIL+1)); FAILED+=("$name"); return
  fi
  echo "✓ $name"
  PASS=$((PASS+1))
}

# ── 端点集（GET 为主；形状=顶层键存在性）──
check "healthz"        "/health"                          'd.get("status") == "ok"'
check "auth/me"        "/api/auth/me"                     '"username" in d'
check "策略列表"        "/api/strategy"                    'isinstance(d, list)'
check "因子库"          "/api/factors"                     '"items" in d'
check "实盘任务"        "/api/live-task"                   'isinstance(d, list)'
check "通知中心"        "/api/notifications"               '"items" in d and "count" in d'
check "Dashboard"      "/api/dashboard"                   'isinstance(d, dict)'
check "风险状态"        "/api/risk/state"                  'isinstance(d, dict)'
check "风控日志"        "/api/risk/log"                    'isinstance(d, dict) or isinstance(d, list)'
check "对账总览"        "/api/reconcile"                   'isinstance(d, dict) or isinstance(d, list)'
check "对账差异单"      "/api/reconcile/issues"            'isinstance(d, dict) or isinstance(d, list)'
check "选股-A股"        "/api/screen/astock"               'isinstance(d, dict) or isinstance(d, list)'
check "选股-转债"       "/api/screen/cb"                   'isinstance(d, dict) or isinstance(d, list)'
check "选股-ETF"        "/api/screen/etf"                  'isinstance(d, dict) or isinstance(d, list)'
check "标的池"          "/api/pool"                        'isinstance(d, dict) or isinstance(d, list)'
check "持仓"            "/api/position"                    'isinstance(d, dict) or isinstance(d, list)'
check "订单"            "/api/orders"                      'isinstance(d, dict) or isinstance(d, list)'
check "回测列表"        "/api/backtest"                    'isinstance(d, dict) or isinstance(d, list)'
check "健康组件"        "/api/health/components"           'isinstance(d, dict) or isinstance(d, list)'
check "帮助-index"      "/api/help/index"                  '"content" in d'
check "runbook 映射"     "/api/runbook"                     '"items" in d and len(d["items"]) > 20'

echo "──────────"
echo "冒烟结果: $PASS 绿 / $FAIL 红"
[ $FAIL -gt 0 ] && { echo "红项: ${FAILED[*]}"; exit 1; }
exit 0
