#!/usr/bin/env bash
# 本地开发环境一键部署（start / stop / status / restart / logs）
#
# 用法：
#   bash scripts/dev-start.sh           # 默认 start（含迁移）
#   bash scripts/dev-start.sh start     # 启动后端 + 前端（先跑 alembic upgrade）
#   bash scripts/dev-start.sh stop      # 停止所有
#   bash scripts/dev-start.sh restart   # 重启
#   bash scripts/dev-start.sh status    # 查看状态
#   bash scripts/dev-start.sh logs      # 查看后端日志（tail -f）
#
# 前置（一次性，已做过则跳过）：
#   sudo bash scripts/init-db.sh        # 建 PG quant 角色/库
#   bash scripts/init-valkey.sh         # 起 Valkey
#   cd server && ./venv/bin/pip install -r requirements.txt
#   cd web && npm install
#
# 详见 scripts/LOCAL-DEPLOY.md
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVER="$ROOT/server"
WEB="$ROOT/web"
VENV="$SERVER/venv/bin/python"
BACKEND_PORT=8000
FRONTEND_PORT=5173
BACKEND_LOG=/tmp/quant-uvicorn.log
FRONTEND_LOG=/tmp/quant-vite.log

ACTION="${1:-start}"

# --- 辅助 ---
green() { printf "\033[32m%s\033[0m\n" "$1"; }
red()   { printf "\033[31m%s\033[0m\n" "$1"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$1"; }

check_prereq() {
  echo "🔍 检查前置依赖..."
  # PG
  if pg_isready -U quant -d quant >/dev/null 2>&1; then
    green "  PG: ✓"
  else
    red "  PG: ✗ 未启动或 quant 角色未建"
    red "  → 运行: sudo bash scripts/init-db.sh"
    return 1
  fi
  # Valkey
  if valkey-cli ping >/dev/null 2>&1 || redis-cli ping >/dev/null 2>&1; then
    green "  Valkey: ✓"
  else
    red "  Valkey: ✗ 未启动"
    red "  → 运行: bash scripts/init-valkey.sh"
    return 1
  fi
  # venv
  if [ ! -x "$VENV" ]; then
    red "  venv: ✗ 不存在 ($VENV)"
    red "  → cd server && python3.10 -m venv venv && ./venv/bin/pip install -r requirements.txt"
    return 1
  fi
  green "  venv: ✓"
  # node_modules
  if [ ! -d "$WEB/node_modules" ]; then
    red "  node_modules: ✗ 不存在"
    red "  → cd web && npm install"
    return 1
  fi
  green "  node_modules: ✓"
}

run_migrations() {
  echo "📦 跑 alembic 迁移..."
  (cd "$SERVER" && "$VENV" -m alembic upgrade head 2>&1 | tail -3) || {
    yellow "  ⚠ alembic 迁移有 warning（可能已最新，无碍）"
  }
}

backend_pid() { pgrep -f "uvicorn src.web_api.main:app --port $BACKEND_PORT" | head -1; }
frontend_pid() { pgrep -f "vite --port $FRONTEND_PORT" | head -1; }

start_backend() {
  if [ -n "$(backend_pid)" ]; then
    yellow "  后端已在运行 (pid $(backend_pid))，跳过"
    return
  fi
  echo "🚀 启动后端 :$BACKEND_PORT ..."
  (cd "$SERVER" && setsid ./venv/bin/uvicorn src.web_api.main:app --port $BACKEND_PORT >"$BACKEND_LOG" 2>&1 < /dev/null &)
  # 等就绪（最多 15s）
  for i in $(seq 1 15); do
    if curl -sf http://127.0.0.1:$BACKEND_PORT/health >/dev/null 2>&1; then
      green "  后端 ✓ (pid $(backend_pid))"
      return
    fi
    sleep 1
  done
  red "  后端启动失败，日志："
  tail -20 "$BACKEND_LOG"
  return 1
}

start_frontend() {
  if [ -n "$(frontend_pid)" ]; then
    yellow "  前端已在运行 (pid $(frontend_pid))，跳过"
    return
  fi
  echo "🚀 启动前端 :$FRONTEND_PORT ..."
  (cd "$WEB" && setsid npx vite --port $FRONTEND_PORT --host 127.0.0.1 >"$FRONTEND_LOG" 2>&1 < /dev/null &)
  for i in $(seq 1 15); do
    if curl -sf http://127.0.0.1:$FRONTEND_PORT/ >/dev/null 2>&1; then
      green "  前端 ✓ (pid $(frontend_pid))"
      return
    fi
    sleep 1
  done
  red "  前端启动失败，日志："
  tail -20 "$FRONTEND_LOG"
  return 1
}

stop_all() {
  echo "🛑 停止..."
  local bp=$(backend_pid) fp=$(frontend_pid)
  [ -n "$bp" ] && { kill "$bp" 2>/dev/null || true; green "  后端已停 (pid $bp)"; } || yellow "  后端未运行"
  [ -n "$fp" ] && { kill "$fp" 2>/dev/null || true; green "  前端已停 (pid $fp)"; } || yellow "  前端未运行"
  sleep 1
}

show_status() {
  echo "📊 状态"
  if [ -n "$(backend_pid)" ]; then
    green "  后端 :$BACKEND_PORT 运行中 (pid $(backend_pid))"
    curl -sf http://127.0.0.1:$BACKEND_PORT/health 2>/dev/null | python3 -c "import sys,json; print('    health:', json.load(sys.stdin))" 2>/dev/null || true
  else
    red "  后端 :$BACKEND_PORT 未运行"
  fi
  if [ -n "$(frontend_pid)" ]; then
    green "  前端 :$FRONTEND_PORT 运行中 (pid $(frontend_pid))"
  else
    red "  前端 :$FRONTEND_PORT 未运行"
  fi
  # 顺便看 PG/Valkey
  pg_isready -U quant -d quant >/dev/null 2>&1 && green "  PG: ✓" || red "  PG: ✗"
  { valkey-cli ping >/dev/null 2>&1 || redis-cli ping >/dev/null 2>&1; } && green "  Valkey: ✓" || red "  Valkey: ✗"
}

tail_logs() {
  echo "📜 后端日志 (Ctrl+C 退出)..."
  if [ -n "$(backend_pid)" ]; then
    tail -f "$BACKEND_LOG"
  else
    red "后端未运行"
    tail -50 "$BACKEND_LOG" 2>/dev/null || echo "无日志"
  fi
}

case "$ACTION" in
  start)
    check_prereq
    run_migrations
    start_backend
    start_frontend
    echo ""
    green "✅ 本地环境已就绪"
    echo "   后端: http://127.0.0.1:$BACKEND_PORT  (health: /health)"
    echo "   前端: http://127.0.0.1:$FRONTEND_PORT"
    echo "   默认账号: admin / admin123"
    echo ""
    echo "   日志: tail -f $BACKEND_LOG  /  $FRONTEND_LOG"
    echo "   停止: bash scripts/dev-start.sh stop"
    ;;
  stop)    stop_all ;;
  restart) stop_all; sleep 1; check_prereq; start_backend; start_frontend; show_status ;;
  status)  show_status ;;
  logs)    tail_logs ;;
  *)
    red "未知命令: $ACTION"
    echo "用法: bash scripts/dev-start.sh [start|stop|restart|status|logs]"
    exit 1
    ;;
esac