#!/usr/bin/env bash
# Valkey/Redis 检查 + 启动提示
set -euo pipefail

if valkey-cli ping 2>/dev/null | grep -q PONG; then
  echo "✓ Valkey 已运行: $(valkey-cli ping)"
elif redis-cli ping 2>/dev/null | grep -q PONG; then
  echo "✓ Redis 已运行: $(redis-cli ping)"
else
  echo "→ Valkey/Redis 未运行，尝试启动 ..."
  if sudo systemctl start valkey 2>/dev/null || sudo systemctl start redis 2>/dev/null; then
    sleep 1
    valkey-cli ping 2>/dev/null || redis-cli ping
  else
    echo "✗ 启动失败，请手动: sudo systemctl start valkey" >&2
    exit 1
  fi
fi

echo "  连接: 127.0.0.1:6379 (无密码，本地开发)"
