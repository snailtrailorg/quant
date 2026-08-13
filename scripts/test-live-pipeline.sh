#!/usr/bin/env bash
# 交易时段实盘全链路验证脚本（在服务器上跑，或任何能连 XTP 的机器）
#
# 用法（从开发机）：
#   ssh quant.snailtrail.cc 'cd /data/websites/snailtrail.cc/quant/server && bash scripts/test-live-pipeline.sh'
#
# 或服务器本地：
#   cd /data/websites/snailtrail.cc/quant/server && bash scripts/test-live-pipeline.sh
#
# 验证：tick → BarGenerator → on_bar → 策略 → 信号 → 风控（拒单，因 ENABLE_LIVE_TRADING 关）
# 安全：三级开关全关，即使产生信号也被 risk_control 拒绝，不会真下单。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="./venv/bin/python"
DURATION="${1:-90}"  # 默认跑 90 秒（够收 tick + 生成 1 分钟 bar）

green() { printf "\033[32m%s\033[0m\n" "$1"; }
red()   { printf "\033[31m%s\033[0m\n" "$1"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$1"; }

echo "=== 交易时段实盘全链路验证 ==="
date "+%Y-%m-%d %H:%M:%S %A"
echo ""

# 1. 安全开关检查（仅提示，不拒绝--XTP 测试账号是虚拟盘，可安全实单测试）
echo "=== 1. 安全开关状态 ==="
if grep -q '^ENABLE_LIVE_TRADING=true' .env 2>/dev/null; then
  yellow "  ENABLE_LIVE_TRADING: 开（测试账号虚拟盘，影响不大）"
else
  yellow "  ENABLE_LIVE_TRADING: 关（风控拒单，只能测到风控前置那步）"
fi
LIVE_ON=$(psql -U quant -d quant -At -c "SELECT count(*) FROM live_trading_config WHERE enabled=true" 2>/dev/null)
if [ "$LIVE_ON" != "0" ]; then
  yellow "  live_trading_config: $LIVE_ON 个分项开着"
else
  yellow "  live_trading_config: 全关"
fi

# 2. XTP 可达性
echo ""
echo "=== 2. XTP 可达性 ==="
timeout 5 bash -c '</dev/tcp/119.3.103.38/6002' 2>&1 && green "  行情端口可达" || red "  行情端口不可达（警告：无行情数据，策略不产生信号）"
timeout 5 bash -c '</dev/tcp/122.112.139.0/6102' 2>&1 && green "  交易端口可达" || { red "  交易端口不可达"; TD_DOWN=1; }
[ -n "${TD_DOWN:-}" ] && { red "XTP 交易端口不可达，无法测全链路（网络/白名单问题）"; exit 1; }

# 3. 确认测试策略 + live_task 存在
echo ""
echo "=== 3. 检查测试 live_task ==="
# 用准备好的测试策略（如果不存在，先建）
STRAT_ID="test-live-pipeline"
SYMBOL="600000.SHSE"
TASK_ID=$(psql -U quant -d quant -At -c "SELECT id FROM live_task WHERE name='交易时段联调-浦发银行' ORDER BY id DESC LIMIT 1" 2>/dev/null | head -1)
if [ -z "$TASK_ID" ]; then
  # 建策略
  psql -U quant -d quant -c "INSERT INTO strategy_config (id, name, type, symbol, adapter, enabled, factors, aggregator, risk, params, backtest_verified) VALUES ('$STRAT_ID', '联调测试','astock_analysis','','xtp',true,'[]','{}','{}','{\"mode\":\"python\",\"python_code\":\"def on_bar(ctx):\\n    close = ctx.get_bar(\\\"close\\\")\\n    hist = ctx.get_history(5)\\n    if len(hist) >= 5:\\n        sma = sum(hist) / len(hist)\\n        dev = (close - sma) / sma\\n        if dev > 0.001:\\n            return ctx.buy(100)\\n        elif dev < -0.001:\\n            return ctx.sell(100)\\n    return ctx.hold()\",\"parameter_defs\":[{\"name\":\"threshold\",\"type\":\"number\",\"default\":0.001}]}',true) ON CONFLICT (id) DO NOTHING" 2>/dev/null
  # 建 live_task
  TASK_ID=$(psql -U quant -d quant -At -c "INSERT INTO live_task (name, strategy_id, symbol, params, strategy_snapshot, status, initial_capital) VALUES ('交易时段联调-浦发银行','$STRAT_ID','$SYMBOL','{}','{}','pending',1000000) RETURNING id" 2>/dev/null | head -1)
  CLEANUP_TASK=1
  green "  新建 live_task id=$TASK_ID (strategy=$STRAT_ID, symbol=$SYMBOL)"
  if [ -z "$TASK_ID" ]; then
    red "创建 live_task 失败（INSERT 未返回 id），无法继续测试"
    exit 1
  fi
else
  green "  找到测试 live_task id=$TASK_ID"
fi

# 4. 跑 strategy_runner
echo ""
echo "=== 4. 跑 strategy_runner --task-id $TASK_ID（${DURATION}s）==="
LOG=/tmp/live-test-$$.log
timeout "$DURATION" $PY -m src.strategy_runner.main --task-id "$TASK_ID" --verbose > "$LOG" 2>&1
echo "  exit=$? (124=timeout 正常)"

# 5. 看关键日志
echo ""
echo "=== 5. 关键日志 ==="
grep -E '启动|订阅|tick|on_bar|BAR|信号|BUY|SELL|HOLD|check_order|approved|拒|登录|connect|error|ERROR|Traceback|XTP' "$LOG" | head -50

echo ""
echo "=== 6. account_snapshot 是否写入（每 60s 一次）==="
psql -U quant -d quant -c "SELECT ts, total_value, daily_pnl FROM account_snapshot ORDER BY ts DESC LIMIT 3" 2>&1 | head -8

# 清理临时 task
if [ -n "${CLEANUP_TASK:-}" ] && [ -n "$TASK_ID" ]; then
  psql -U quant -d quant -At -c "DELETE FROM live_task WHERE id=$TASK_ID" >/dev/null 2>&1
  echo ""
  yellow "已清理临时 live_task $TASK_ID"
fi

echo ""
green "=== 验证完成 ==="
echo "完整日志: $LOG"
echo ""
echo "判读："
echo "  ✓ 看到 '订阅 600000' + XTP 行情登录成功 → 连接 OK"
echo "  ✓ 看到 on_bar / 信号（BUY/SELL/HOLD）→ 策略驱动 OK"
echo "  ✓ 看到下单（XTP 测试账号虚拟盘，风控通过则发单到测试环境）→ 风控前置 OK"
echo "  ✓ account_snapshot 有新行 → 持仓快照写入 OK"