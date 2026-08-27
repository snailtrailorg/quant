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
    # 条件轮询（P2-2，守则原则 1）：PONG 即出，5s 兜底真异常——不猜启动耗时
    ok=""
    for _ in $(seq 1 10); do
      if valkey-cli ping 2>/dev/null | grep -q PONG; then ok=valkey; break; fi
      if redis-cli ping 2>/dev/null | grep -q PONG; then ok=redis;  break; fi
      sleep 0.5
    done
    if [ -n "$ok" ]; then
      echo "✓ ${ok}-cli 已就绪"
    else
      echo "✗ 启动后 5s 未 PONG，请手动: systemctl status valkey" >&2
      exit 1
    fi
  else
    echo "✗ 启动失败，请手动: sudo systemctl start valkey" >&2
    exit 1
  fi
fi

echo "  连接: 127.0.0.1:6379 (无密码，本地开发)"
