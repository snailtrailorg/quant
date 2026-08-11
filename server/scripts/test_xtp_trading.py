#!/usr/bin/env python3
"""本地/服务器验证 XTPAdapter 实盘下单全接口（连接+资金+持仓+下单+撤单+委托/成交）。

前置：
  1. .env ENABLE_LIVE_TRADING=true（总闸）
  2. Web PUT /api/live-trading/etf?enabled=true（分项，或直接 DB 改）
  3. 交易时段（周一-五 9:30-15:00）下单才有效，非交易时段会被拒
  4. LD_LIBRARY_PATH=vendor/xtp/lib（import vnpy_xtp 要加载 .so）

运行（服务器）：
  cd /data/websites/snailtrail.cc/quant/server
  LD_LIBRARY_PATH=$PWD/vendor/xtp/lib QT_QPA_PLATFORM=offscreen venv/bin/python scripts/test_xtp_trading.py

A 股三重只读铁律：本脚本只测可转债/ETF（510050），不碰 A 股股票。
"""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 前置：检查三级开关（.env 总闸 + Web 分项）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.data_platform.settings import is_live_trading_enabled
from src.risk_control.risk import RiskControl

if not is_live_trading_enabled():
    print("❌ .env ENABLE_LIVE_TRADING=false（总闸关），下单会被风控拒。设 true 再跑。")
    sys.exit(1)

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy_xtp import XtpGateway
from src.strategy_framework.adapters import XTPAdapter, Order

import os
from dotenv import load_dotenv
load_dotenv()
SETTING = {
    "账号": os.environ.get("XTP_TEST_ACCOUNT", ""),
    "密码": os.environ.get("XTP_TEST_PASSWORD", ""),
    "客户号": int(os.environ.get("XTP_TEST_CLIENT_ID", "1")),
    "行情地址": os.environ.get("XTP_TEST_QUOTE_HOST", ""),
    "行情端口": int(os.environ.get("XTP_TEST_QUOTE_PORT", "0")),
    "交易地址": os.environ.get("XTP_TEST_TRADE_HOST", ""),
    "交易端口": int(os.environ.get("XTP_TEST_TRADE_PORT", "0")),
    "行情协议": "TCP",
    "日志级别": "INFO",
    "授权码": os.environ.get("XTP_TEST_KEY", ""),
}

# 测试标的：510050.SSE 上证50ETF（小金额，限价 3.00 买 100 股 ~300 元）
TEST_SYMBOL = "510050.SSE"
TEST_PRICE = 3.00
TEST_VOLUME = 100

print("=== 启动 EventEngine + MainEngine ===")
ee = EventEngine()
me = MainEngine(ee)
me.add_gateway(XtpGateway)
me.connect(SETTING, "XTP")
print("=== 等待 8s 登录 ===")
time.sleep(8)

gateway = me.get_gateway("XTP")
adapter = XTPAdapter(gateway=gateway, event_engine=ee)

# 1. 查资金
print("\n=== 1. query_account ===")
accts = adapter.query_account()
for a in accts:
    print(f"  account={getattr(a, 'accountid', '?')} balance={getattr(a, 'balance', '?')} available={getattr(a, 'available', '?')}")

# 2. 查持仓
print("\n=== 2. query_position ===")
positions = adapter.query_position()
if not positions:
    print("  （无持仓）")
for p in positions:
    print(f"  {p.symbol} volume={p.volume} avg_price={p.avg_price} pnl={p.pnl}")

# 3. 风控前置检查（三级开关 + 熔断 + 全局）
print("\n=== 3. check_order（三级开关风控）===")
rc = RiskControl.get()
order_dict = {"symbol": TEST_SYMBOL, "action": "BUY", "volume": TEST_VOLUME, "price": TEST_PRICE}
decision = rc.check_order(order_dict, "")
print(f"  approved={decision.approved} reason={decision.reason} severity={decision.severity}")
if not decision.approved:
    print("❌ 风控拒单，不下单。检查 Web 分项 etf 是否开 + 熔断状态。")
    me.close()
    sys.exit(1)

# 4. 下单
print(f"\n=== 4. send_order {TEST_SYMBOL} 限价 {TEST_PRICE} 买 {TEST_VOLUME} ===")
order = Order(symbol=TEST_SYMBOL, action="BUY", volume=TEST_VOLUME, price=TEST_PRICE, order_type="limit")
client_id = adapter.send_order(order)
print(f"  client_id={client_id}")
time.sleep(3)

# 5. 查委托/成交
print("\n=== 5. query_orders ===")
orders = adapter.query_orders()
for o in orders:
    print(f"  {getattr(o,'vt_orderid','?')} {getattr(o,'symbol','?')} {getattr(o,'direction','?')} status={getattr(o,'status','?')} price={getattr(o,'price','?')} vol={getattr(o,'volume','?')}")

print("\n=== 6. query_trades ===")
trades = adapter.query_trades()
for t in trades:
    print(f"  {getattr(t,'vt_tradeid','?')} {getattr(t,'symbol','?')} price={getattr(t,'price','?')} vol={getattr(t,'volume','?')}")

# 7. 撤单
print(f"\n=== 7. cancel_order {client_id} ===")
adapter.cancel_order(client_id)
time.sleep(2)

print("\n=== 8. query_orders after cancel ===")
for o in adapter.query_orders():
    print(f"  {getattr(o,'vt_orderid','?')} status={getattr(o,'status','?')}")

print("\n=== 关闭（close core dump 是 vnpy_xtp 析构 bug，预期，忽略）===")
me.close()
print("=== 完成（实盘下单全接口验证）===")
