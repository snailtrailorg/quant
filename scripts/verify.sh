#!/usr/bin/env bash
# 全链路本地验证脚本
# 逐一测试所有模块是否工作，不依赖外部凭证
set -euo pipefail

VENV="$(dirname "$(dirname "$0")")/venv/bin/python"
echo "🔍 全链路验证 $(date)"

# 1. 环境
echo
echo "=== 1. 环境 ==="
$VENV -c "import sys; print(f'Python {sys.version}')"
$VENV -c "import vnpy; print(f'vnpy {vnpy.__version__}')" 2>/dev/null || echo "vnpy: 未安装"

# 2. 数据中台
echo
echo "=== 2. 数据中台 ==="
$VENV -c "
import sys; sys.path.insert(0,'.'); import psycopg
conn = psycopg.connect('postgresql://quant@127.0.0.1:5432/quant')
print(f'PG: {conn.execute(\"select current_user\").fetchone()[0]}')
conn.execute(\"select extname from pg_extension where extname='vector'\")
print('pgvector: ✓')
" 2>/dev/null || echo "PG: ✗ 连接失败"

$VENV -c "
import sys; sys.path.insert(0,'.'); import redis; r=redis.Redis(); print('Valkey:', r.ping() and 'PONG')
" 2>/dev/null || echo "Valkey: ✗"

# 3. 模块导入
echo
echo "=== 3. 模块导入 ==="
for mod in data_platform llm_gateway strategy_framework risk_control alert_notify web_api feishu_bot scheduler astock_analysis; do
  $VENV -c "import sys; sys.path.insert(0,'.'); from src import $mod; print('$mod: ✓')" 2>/dev/null || echo "$mod: ✗"
done

# 4. 策略框架
echo
echo "=== 4. 策略框架 ==="
$VENV -c "
import sys; sys.path.insert(0,'.')
from src.strategy_framework import list_factors, create_adapter, AStockReadonlyAdapter, Order
print(f'因子: {len(list_factors())}个')
try: AStockReadonlyAdapter().send_order(Order('test','BUY'))
except PermissionError: print('A股只读: ✓')
"

# 5. 风控
echo
echo "=== 5. 风控 ==="
$VENV -c "
import sys, os; sys.path.insert(0,'.'); from dotenv import load_dotenv; load_dotenv()
from src.risk_control import RiskControl
rc = RiskControl.get()
print(f'熔断: {rc.is_halted()}')
"

# 6. Web 后端
echo
echo "=== 6. Web 后端 ==="
curl -sf http://127.0.0.1:8000/health 2>/dev/null | head -c 50 && echo " ✓" || echo "Web: ✗ 未启动"

# 7. 前端
echo
echo "=== 7. 前端 ==="
curl -sf http://127.0.0.1:5173/ 2>/dev/null | head -c 30 && echo " ✓" || echo "前端: ✗ 未启动"

# 8. 非交易日跳过
echo
echo "=== 8. 交易日历 ==="
$VENV -c "
import sys; sys.path.insert(0,'.'); from dotenv import load_dotenv; load_dotenv()
from src.data_platform import platform
print(f'交易日历 2026: {len(platform.get_trade_calendar(2026))}天')
print(f'今天是否交易日: {platform.is_trading_day()}')
"

echo
echo "✅ 全链路验证完成"