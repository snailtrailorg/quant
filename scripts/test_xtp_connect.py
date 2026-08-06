#!/usr/bin/env python3
"""本地验证 XtpGateway 连中泰测试账户（仅连接/登录/订阅行情，绝不下单）。
A 股三重只读铁律：此脚本不触发任何下单调用。
运行：LD_LIBRARY_PATH=vendor/xtp/lib QT_QPA_PLATFORM=offscreen venv/bin/python scripts/test_xtp_connect.py
"""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from vnpy.event import EventEngine, Event
from vnpy.trader.engine import MainEngine
from vnpy.trader.event import EVENT_LOG, EVENT_CONTRACT, EVENT_TICK, EVENT_ACCOUNT
from vnpy.trader.object import SubscribeRequest
from vnpy.trader.constant import Exchange
from vnpy_xtp import XtpGateway

SETTING = {
    "账号": "253191001822",
    "密码": "Xkih9pt2",
    "客户号": 1,
    "行情地址": "119.3.103.38",
    "行情端口": 6002,
    "交易地址": "122.112.139.0",
    "交易端口": 6102,
    "行情协议": "TCP",
    "日志级别": "INFO",
    "授权码": "b8aa7173bba3470e390d787219b2112e",
}

def on_event(event: Event):
    data = event.data
    # 只打印关键信息，避免刷屏
    if event.type == EVENT_TICK:
        print(f"[TICK] {getattr(data,'vt_symbol','?')} last={getattr(data,'last_price','?')} ")
    elif event.type == EVENT_CONTRACT:
        print(f"[CONTRACT] {getattr(data,'vt_symbol','?')}")
    elif event.type == EVENT_ACCOUNT:
        print(f"[ACCOUNT] {getattr(data,'accountid','?')} balance={getattr(data,'balance','?')}")
    else:  # EVENT_LOG 等
        msg = getattr(data, 'msg', data) if not isinstance(data, str) else data
        print(f"[{event.type}] {msg}")

print("=== 启动 EventEngine + MainEngine ===")
ee = EventEngine()
me = MainEngine(ee)
me.add_gateway(XtpGateway)

ee.register(EVENT_LOG, on_event)
ee.register(EVENT_CONTRACT, on_event)
ee.register(EVENT_TICK, on_event)
ee.register(EVENT_ACCOUNT, on_event)

print("=== connect XTP（交易 122.112.139.0:6102 / 行情 119.3.103.38:6002）===")
me.connect(SETTING, "XTP")

print("=== 等待 20s 看登录/合约回调（给慢握手留余量）===")
time.sleep(20)

print("=== 订阅行情（510050.SSE 上证50ETF + 113001.SSE 转债）===")
reqs = [
    SubscribeRequest(symbol="510050", exchange=Exchange.SSE),
    SubscribeRequest(symbol="113001", exchange=Exchange.SSE),
]
for req in reqs:
    me.subscribe(req, "XTP")

print("=== 等待 10s 看 tick ===")
time.sleep(10)

print("=== 关闭 ===")
me.close()
print("=== 完成（未触发任何下单）===")
