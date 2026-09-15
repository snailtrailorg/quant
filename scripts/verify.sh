#!/usr/bin/env bash
# 全链路本地验证脚本
# 逐一测试所有模块是否工作，不依赖外部凭证
# 批26-10 三层修复：①venv 指向 server/venv（原指仓库根 venv——第 12 行即 127 死，建仓起未活过）
# ②第 4 段 AStockReadonlyAdapter 2026-08-03 已废止（A股走 XTPAdapter+实盘三级开关）→ 改验证现存面
# ③逐段 || 兜底（set -euo pipefail 下单点失败不中断全链报告）
set -uo pipefail   # 批26-10：去 -e——逐段自报 ✓/✗；退出码恒 0（本地缺 PG/Valkey 段属预期），FAIL 只驱动末尾汇总文案

ROOT="$(cd "$(dirname "$0")/.." && pwd)"   # 先以 $0 算绝对根，再 cd（顺序反了相对路径会错）
cd "$ROOT/server"
VENV="$ROOT/server/venv/bin/python"
echo "🔍 全链路验证 $(date)"

FAIL=0
note_fail() { FAIL=1; }

# 1. 环境
echo
echo "=== 1. 环境 ==="
$VENV -c "import sys; print(f'Python {sys.version}')" || note_fail
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
" 2>/dev/null || { echo "PG: ✗ 连接失败"; note_fail; }

$VENV -c "
import sys; sys.path.insert(0,'.'); import redis; r=redis.Redis(); print('Valkey:', r.ping() and 'PONG')
" 2>/dev/null || { echo "Valkey: ✗"; note_fail; }

# 3. 模块导入
echo
echo "=== 3. 模块导入 ==="
for mod in data_platform llm_gateway strategy_framework risk_control alert_notify web_api feishu_bot scheduler astock_analysis; do
  $VENV -c "import sys; sys.path.insert(0,'.'); from src import $mod; print('$mod: ✓')" 2>/dev/null || { echo "$mod: ✗"; note_fail; }
done

# 4. 策略框架（批26-10：验证现存面——AStockReadonlyAdapter 已废止，A股只读语义在实盘三级开关，
#    不再适配备层 PermissionError 断言）
echo
echo "=== 4. 策略框架 ==="
$VENV -c "
import sys; sys.path.insert(0,'.')
from src.strategy_framework import list_factors, create_adapter, Order
factors = list_factors()
print(f'因子注册: {len(factors)}个')
assert factors, '因子注册表为空'
a = create_adapter('xtp')
print(f'XTPAdapter: {type(a).__name__} ✓')
o = Order('test', 'BUY')
print(f'Order 可构造: {o.symbol} ✓')
" 2>/dev/null || { echo "策略框架: ✗"; note_fail; }

# 5. 风控
echo
echo "=== 5. 风控 ==="
$VENV -c "
import sys, os; sys.path.insert(0,'.'); from dotenv import load_dotenv; load_dotenv()
from src.risk_control import RiskControl
rc = RiskControl.get()
print(f'熔断: {rc.is_halted()}')
" 2>/dev/null || { echo "风控: ✗（本地无 .env/Valkey 属预期）"; note_fail; }

# 6. Web 后端
echo
echo "=== 6. Web 后端 ==="
curl -sf http://127.0.0.1:8000/health 2>/dev/null | head -c 50 && echo " ✓" || { echo "Web: ✗ 未启动"; note_fail; }

# 7. 前端
echo
echo "=== 7. 前端 ==="
curl -sf http://127.0.0.1:5173/ 2>/dev/null | head -c 30 && echo " ✓" || { echo "前端: ✗ 未启动"; note_fail; }

# 8. 非交易日跳过
echo
echo "=== 8. 交易日历 ==="
$VENV -c "
import sys; sys.path.insert(0,'.'); from dotenv import load_dotenv; load_dotenv()
from src.data_platform import platform
print(f'交易日历 2026: {len(platform.get_trade_calendar(2026))}天')
print(f'今天是否交易日: {platform.is_trading_day()}')
" 2>/dev/null || { echo "交易日历: ✗（本地无缓存属预期）"; note_fail; }

echo
if [ "$FAIL" -eq 0 ]; then
  echo "✅ 全链路验证完成（全绿）"
else
  echo "⚠️  全链路验证完成（有 ✗ 段——本地未起服务/无 DB 时部分段属预期）"
fi
exit 0   # 恒 0（盲审 B-P2-1 核正）：环境缺失段属预期非失败；CI 网关请看段级输出
