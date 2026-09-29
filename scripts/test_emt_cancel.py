#!/usr/bin/env python3
"""EMT 极速柜台验证脚本（批 63 P4 撤单链补验——可复用交付物，2026-09-29）。

验证 EmtAdapter 的撤单链（作战单 `flow/周一观察窗-20260928.md` §3 欠账）：
挂限价单（不成交价）→ 撤单 → OnOrderEvent 回报 CANCELLED 终态（8 值状态映射）。

两个模式：
  --check（默认）  Login → QueryAsset → QueryPosition → Logout。零下单，随时可跑。
  --cancel-test    上述 + 挂限价单 → 等在场 → 撤单 → 观察 CANCELLED 终态 → Logout。

═══ 运行环境（quant 账户，工件化目录结构）═══
  服务器（staging/prod，东财测试柜台网络可达）：
      /data/websites/snailtrail.cc/quant/shared/venv/bin/python /home/quant/scripts/test_emt_cancel.py
      （默认 provider=emt_emq 自动查 enabled 行；可用 --row-id N 显式指定）
    - 脚本已显式 load_dotenv(shared/.env)（见下方 import 前代码），QUANT_DB_URL/SECRET_KEY
      自动就位——不必 cd；db.py/crypto.py 模块级 load_dotenv() 从代码目录向上找不到
      shared/.env（.env 与代码分离），服务进程靠 systemd EnvironmentFile 注入，独立脚本须手动加载。
    - venv=shared/venv（Python 3.11，.so 为 cpython-311）；代码走 server 符号链接
      （→ releases/<id>，本脚本自动探测标准路径 /data/websites/snailtrail.cc/quant/server）。
    - 脚本文件需先落到服务器（如 /home/quant/scripts/，与 backup-db.sh 同目录；
      scripts/ 目录本身不随 Ansible 传服务器）。

  开发机 Fedora（仅 --check 可跑，撤单链因家宽 IP 白名单外 TCP 不通）：
      cd server && venv/bin/python ../scripts/test_emt_cancel.py

═══ 运行前提（五要素）═══
  盘中：A 股交易时段（9:30-11:30 / 13:00-15:00）；午休/盘前柜台不接受委托与撤单。
  账号：external_interface emt_emq 行凭证已落位（批 63 ④a，staging 行 id=6）。
  权限：以 quant 用户跑（shared/.env 是 quant 700）。
  授权：--cancel-test 产生真实委托（不成交价挂单→撤单），执行前须用户明确授权。

环境变量：QUANT_SERVER_DIR 可覆盖 server 目录（默认按服务器标准路径→开发机 ../server 兜底）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # vnpy 兜底，防拉 Qt

_SERVER_STANDARD = "/data/websites/snailtrail.cc/quant/server"  # 服务器符号链接 → releases/<id>


def _find_server_dir() -> str:
    """server 目录探测：env 覆盖 → 服务器标准路径 → 开发机 ../server 兜底。"""
    env = os.environ.get("QUANT_SERVER_DIR")
    if env:
        return env
    if os.path.isdir(_SERVER_STANDARD):
        return _SERVER_STANDARD
    return str(Path(__file__).resolve().parent.parent / "server")


SERVER_DIR = _find_server_dir()
sys.path.insert(0, SERVER_DIR)

# 显式加载 .env：db.py/crypto.py 模块级 load_dotenv() 从代码目录向上找 .env，工件化布局下
# 找不到 shared/.env（.env 与代码分离）——服务进程靠 systemd EnvironmentFile 注入，
# 独立脚本须先手动 load_dotenv 把 QUANT_DB_URL/SECRET_KEY 灌进环境。
from dotenv import load_dotenv as _load_dotenv  # noqa: E402
for _p in ("/data/websites/snailtrail.cc/quant/shared/.env", os.path.join(SERVER_DIR, ".env")):
    if os.path.isfile(_p):
        _load_dotenv(_p)
        break

from vnpy.event import EventEngine, Event  # noqa: E402
from vnpy.trader.event import EVENT_ORDER  # noqa: E402
from vnpy.trader.constant import Status  # noqa: E402

from src.strategy_framework.adapters import EmtAdapter, Order  # noqa: E402
from src.strategy_framework.broker import get_interface_row  # noqa: E402
from src.strategy_framework.md_gateway import _parse_host_port  # noqa: E402

# client_id 避开 EMT worker 派生段 20-119（td_registry (tid-1)%100+20）与 XTP 2-99
_DEFAULT_TD_CLIENT_ID = 120


def _find_row_id(provider: str) -> int:
    """按 provider 找第一个 enabled 行（独立脚本无固定行 id——staging/prod 行 id 不同）。"""
    from src.data_platform.db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM external_interface WHERE provider=%s AND enabled=true ORDER BY id",
            (provider,)).fetchall()
    if not rows:
        with get_conn() as conn:
            allrows = conn.execute(
                "SELECT id, provider, enabled FROM external_interface ORDER BY id").fetchall()
        raise SystemExit(f"[FAIL] 无 provider={provider} 的 enabled 行。全表：{allrows}")
    if len(rows) > 1:
        print(f"[WARN] provider={provider} 有 {len(rows)} 个 enabled 行（{rows}），取第一个")
    return rows[0][0]


def _build_setting(provider: str, row_id: int | None) -> tuple[dict, str, int]:
    """读 external_interface 行组装 EMT setting（复用 td_registry._build_emt_runtime 逻辑）。"""
    if row_id is None:
        row_id = _find_row_id(provider)
    row = get_interface_row(row_id=row_id)
    cred = row.get("credentials") or {}
    params = row.get("params") or {}
    host, port = _parse_host_port(params.get("emt_td_host", ""), default_port=19088)
    setting = {"td_host": host, "td_port": port,
               "account": str(cred.get("emt_account", "")),
               "password": str(cred.get("emt_password", ""))}
    if not (host and setting["account"] and setting["password"]):
        raise SystemExit(
            f"[FAIL] {provider} 行 id={row_id} 凭证不完整"
            f"（host={host!r} account 已设?={bool(setting['account'])}"
            f" pwd 已设?={bool(setting['password'])}）——先落位 ④a 凭证")
    return setting, row.get("provider", provider), row_id


def _wait_status(adapter: EmtAdapter, client_id: str, statuses: set, timeout: float,
                 tag: str) -> str:
    """轮询 query_orders 等 client_id 对应 vt_orderid 进入目标状态集。返回最终状态名。"""
    deadline = time.monotonic() + timeout
    vt = adapter.get_vt_orderid(client_id)
    while time.monotonic() < deadline:
        for od in adapter.query_orders():
            if vt is None or od.vt_orderid == vt:
                vt = od.vt_orderid
                if od.status in statuses:
                    print(f"[OK] {tag}：{vt} status={od.status.value} "
                          f"traded={od.traded}/{od.volume}")
                    return od.status.value
        time.sleep(0.5)
    print(f"[WARN] {tag} 超时（{timeout}s）未见目标状态 {statuses}，当前在场单：")
    for od in adapter.query_orders():
        print(f"      {od.vt_orderid} status={od.status.value} traded={od.traded}/{od.volume}")
    return "timeout"


def main() -> int:
    ap = argparse.ArgumentParser(description="EMT 柜台验证（撤单链补验）")
    ap.add_argument("--provider", default="emt_emq", help="external_interface provider（默认 emt_emq）")
    ap.add_argument("--row-id", type=int, default=None, help="external_interface 行 id（缺省按 provider 自动查）")
    ap.add_argument("--cancel-test", action="store_true", help="执行撤单链验证（真实委托，需授权）")
    ap.add_argument("--symbol", default="510300.SHSE", help="挂单标的（vt_symbol 格式）")
    ap.add_argument("--price", type=float, default=1.00, help="限价（不成交价——远低于现价的买单价）")
    ap.add_argument("--volume", type=int, default=100, help="挂单量（股，100 整数倍）")
    ap.add_argument("--td-client-id", type=int, default=_DEFAULT_TD_CLIENT_ID,
                    help="SDK client_id（u8 域 [1-127]，避 worker 段 20-119）")
    args = ap.parse_args()

    print(f"[env] server_dir={SERVER_DIR}")

    setting, provider, row_id = _build_setting(args.provider, args.row_id)
    print(f"=== EMT 柜台（provider={provider} row_id={row_id}）===")
    print(f"    地址 {setting['td_host']}:{setting['td_port']} 账号 {setting['account']}")

    ee = EventEngine()
    ee.start()

    def on_order(event: Event):
        od = event.data
        print(f"[ORDER] {od.vt_orderid} status={od.status.value} "
              f"dir={od.direction.value} price={od.price} vol={od.volume} traded={od.traded}")

    ee.register(EVENT_ORDER, on_order)

    adapter = EmtAdapter(event_engine=ee, order_prefix="cancel-test:",
                         td_client_id=args.td_client_id)

    print("=== Login ===")
    adapter.connect(setting)
    print(f"[OK] Login session={adapter._session_id} trading_day={adapter._trading_day}")
    time.sleep(2)

    print("=== QueryAsset ===")
    for a in adapter.query_account():
        print(f"[OK] account balance={a.balance} available={a.available} frozen={a.frozen}")

    print("=== QueryPosition ===")
    pos = adapter.query_position()
    print(f"[OK] 持仓 {len(pos)} 行" + ("（空）" if not pos else ""))
    for p in pos:
        print(f"     {p.symbol} vol={p.volume} avg={p.avg_price} frozen={p.frozen}")

    if not args.cancel_test:
        print("=== 仅健康检查（--check 默认），不挂单。撤单链验证请加 --cancel-test ===")
        adapter._api.Logout(adapter._session_id)
        ee.stop()   # 停 EventEngine 后台线程，否则非守护线程挂住进程不退出
        return 0

    # ── 撤单链验证 ──
    client_id = f"cancel-test-{int(time.time())}"
    order = Order(symbol=args.symbol, action="BUY", volume=args.volume,
                  price=args.price, order_type="limit", client_id=client_id)
    print(f"=== 挂限价单（不成交价）{args.symbol} BUY {args.volume}@{args.price} ===")
    ret = adapter.send_order(order)
    if not ret:
        print("[FAIL] 委托未发出（emt_id=0）——查 GetApiLastError")
        return 1
    print(f"[OK] send_order 返 client_id={ret}")

    _wait_status(adapter, client_id, {Status.NOTTRADED, Status.SUBMITTING}, 10, "委托在场")

    print(f"=== 撤单 {client_id} ===")
    adapter.cancel_order(client_id)

    final = _wait_status(adapter, client_id, {Status.CANCELLED, Status.ALLTRADED}, 10,
                         "撤单终态")
    print("=== Logout ===")
    adapter._api.Logout(adapter._session_id)
    ee.stop()

    if final == Status.CANCELLED.value:
        print("=== 撤单链验证通过：CANCELLED 终态回报链正常（8 值状态映射 OK）===")
        return 0
    if final == Status.ALLTRADED.value:
        print("=== 撤单链验证未达预期：订单已成交（价格非不成交价），无法验证撤单 ===")
        return 1
    print(f"=== 撤单链验证失败：终态={final}（预期 cancelled）===")
    return 1


if __name__ == "__main__":
    sys.exit(main())
