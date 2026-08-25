#!/usr/bin/env python3
"""MD 生命周期真机冒烟（批 1 四道门之 G2，2026-08-25）。

目的：mock 单测拦不住 C 层（473 绿照样 SEGV 的教训）——本脚本用**真实进程**连
中泰 XTP 测试平台，走完 GuardedXtpMdApi 的完整生命周期：
    登录 → 收 tick → relogin 官方往返（Logout→Login）→ 重新订阅 → 再收 tick → 干净退出
任何 SEGV/abort 都在本门暴露。测试平台 7×24 有行情回放，非交易时段可跑。

A 股三重只读铁律：本脚本绝不触发任何交易接口（无 TD 构造、无下单调用）。

运行（server/ 目录下，本脚本随 rsync 部署、两机同路径）：
  LD_LIBRARY_PATH=vendor/xtp/lib QT_QPA_PLATFORM=offscreen \
      venv/bin/python scripts/run_md_lifecycle.py [--wait N]
（部署前服务器暂存跑法：/tmp/b1 前置 PYTHONPATH，见 docs/任务/批1-*.md）
验收：退出码 0，输出含「登录成功」「tick>0」「relogin 往返 OK」「干净退出」。
"""
import argparse
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_HERE = os.path.dirname(os.path.abspath(__file__))
# server/src 定位：默认仓库相对（开发机）；服务器暂存跑法用 QUANT_SERVER_DIR 指生产目录
_SRV = os.environ.get("QUANT_SERVER_DIR") or os.path.normpath(os.path.join(_HERE, "..", "..", "server"))
if os.path.isdir(_SRV):
    sys.path.insert(0, _SRV)

# 测试平台凭证：沿用 scripts/test_xtp_connect.py 的测试账户约定（仓库既有，非新增暴露）
SETTING = {
    "账号": "253191001822",
    "密码": "Xkih9pt2",
    "客户号": 1,
    "行情地址": "119.3.103.38",
    "行情端口": 6002,
    "行情协议": "TCP",
    "日志级别": 3,   # XTP_LOG_LEVEL_FATAL，冒烟降噪
}
SYMBOLS = [("600000", "SSE"), ("000001", "SZSE")]   # 沪深各一只高流动


def main() -> int:
    from vnpy.event import EventEngine
    from vnpy.trader.gateway import BaseGateway
    from vnpy.trader.object import SubscribeRequest
    from vnpy.trader.constant import Exchange
    from src.strategy_framework.md_api_guard import GuardedXtpMdApi, SdkState

    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=30, help="每段等 tick 上限秒数")
    args = ap.parse_args()

    state = {"ticks": 0, "first_ts": 0.0}

    ee = EventEngine()

    class _Gw(BaseGateway):
        """最小事件面：tick 计数 + 日志直打（复用 hub ThinGateway 思路，无 TD）。"""

        def connect(self, setting: dict) -> None: ...
        def subscribe(self, req) -> None: ...
        def send_order(self, req) -> str: return ""
        def cancel_order(self, req) -> None: ...
        def query_account(self) -> None: ...
        def query_position(self) -> None: ...
        def close(self) -> None: ...

    gw = _Gw(ee, "XTP")
    md = GuardedXtpMdApi(gw)

    from vnpy.event import Event
    from vnpy.trader.event import EVENT_LOG, EVENT_TICK

    def _on_log(event: Event):
        print(f"  [gw] {event.data}")
    ee.register(EVENT_LOG, _on_log)

    def _on_tick(event: Event):
        state["ticks"] += 1
        if not state["first_ts"]:
            state["first_ts"] = time.time()
    ee.register(EVENT_TICK, _on_tick)
    ee.start()

    def _sub_all():
        for sym, ex in SYMBOLS:
            gw.md_api.subscribe(SubscribeRequest(symbol=sym, exchange=Exchange(ex)))
    gw.md_api = md

    # ——— 1. 登录（同步返回）———
    md.connect(SETTING["账号"], SETTING["密码"], SETTING["客户号"],
               SETTING["行情地址"], int(SETTING["行情端口"]),
               SETTING["行情协议"], SETTING["日志级别"])
    if md.state is not SdkState.LOGGED_IN or not md.login_status:
        err = md.getApiLastError() or {}
        print(f"❌ 登录失败：state={md.state.value} login_status={md.login_status} "
              f"SDK错误={err.get('error_id')}:{err.get('error_msg')}（"
              f"常见：服务端会话槽被占 user already exists——服务器 hub/任务在用同账号，"
              f"或测试平台不可达）")
        return _exit_fail(ee)
    print("✅ 登录成功（官方时序 createQuoteApi→heartbeat→login）")

    # ——— 2. 首轮收 tick ———
    _sub_all()
    if not _wait_ticks(state, args.wait, state["ticks"], 1):
        return _exit_fail(ee)
    base = state["ticks"]
    print(f"✅ tick>0（首轮 {base} 条）")

    # ——— 3. relogin 官方往返（Logout→Login，活跃会话上执行——正是半开陷阱的时序）———
    if not md.relogin():
        print("❌ relogin 未确认（服务端会话槽异常？间隔 60s 后重跑一次）")
        return _exit_fail(ee)
    # 官方文档：重新登录后必须重新订阅（xtpx_quote_api.h:338）
    _sub_all()
    if not _wait_ticks(state, args.wait, base, 1):
        return _exit_fail(ee)
    print(f"✅ relogin 往返 OK（往返后再收 {state['ticks'] - base} 条 tick）")

    # ——— 4. 干净退出（本进程不触发 exit()/Release，进程级验证到此为止）———
    ee.stop()
    print(f"✅ 干净退出（总 tick {state['ticks']}，零 SEGV/零 abort）")
    return 0


def _exit_fail(ee) -> int:
    """失败路径：先停事件引擎再退（其线程非 daemon，不 stop 会挂住进程）。"""
    ee.stop()
    return 1


def _wait_ticks(state: dict, timeout: int, baseline: int, need: int) -> bool:
    """轮询等待新增 tick；超时 fail-fast。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if state["ticks"] - baseline >= need:
            return True
        time.sleep(1)
    print(f"❌ {timeout}s 内未再收到 tick（baseline={baseline} last={state['ticks']}）——"
          f"查测试平台回放状态或订阅符号")
    return False


if __name__ == "__main__":
    sys.exit(main())
