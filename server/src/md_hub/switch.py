"""M5 数据源切换编排器（批60 方案集 v14）。

子命令：
  confirm <target>  阻塞式切换（quant→quant2 正切 / quant2→quant 反切）
  check             检查切换状态（reaper 手动）
  abort             中止切换（仅 SET intent 后、start 目标前）
  diff <date>       首日 bar 与腾讯回补 bar_1min 全量口径比对

退出码：0=成功 / 2=有 running 任务 / 3=等让位超时 / 4=等接管超时 / 5=target 非法

权限：start 目标经 quant-svc wrapper（systemctl 白名单通道）；
confirm 阻塞驻留，须 `setsid switch.py confirm <target>` 绝缘运行（关终端不杀）。
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import time

GEN_KEY = "hub:gen"
LEASE_KEY = "hub:lease"
INTENT_KEY = "hub:switch:intent"
ACTIVE_INSTANCE_KEY = "hub:active_instance"
HB_KEY = "quant:hb:md-hub"
INTENT_TTL = 300
VALID_TARGETS = ("quant", "quant2")
QUANT_SVC = "/usr/local/sbin/quant-svc"   # systemctl 白名单通道（deploy/wrappers/quant-svc）

_SET_INTENT_LUA = """
local g = redis.call('get', KEYS[1]) or '0'
local payload = cjson.encode({snapshot = tonumber(g), target = ARGV[1]})
redis.call('set', KEYS[2], payload, 'EX', ARGV[2])
return g
"""


def _make_valkey():
    from src.strategy_framework.runtime.alerts import make_valkey
    return make_valkey()


def _v(raw) -> str | None:
    """redis 返回 bytes/str/None → str/None。"""
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def _get(r, key) -> str | None:
    try:
        return _v(r.get(key))
    except Exception as e:
        print(f"⚠ 读 {key} 失败: {e}", file=sys.stderr)
        return None


def _read_intent(r) -> dict | None:
    raw = _get(r, INTENT_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _read_hb_gen(r) -> int | None:
    """读心跳键的 gen 字段（数据流证据，防假接管）。"""
    raw = _get(r, HB_KEY)
    if not raw:
        return None
    try:
        return int(json.loads(raw).get("gen", -1))
    except Exception:
        return None


def _wait_hb_gen(r, expected: int, timeout: float) -> int | None:
    """轮询心跳 gen >= expected（B 心跳 ~5-15s 后首跳，单次读必撞陈旧心跳，须轮询）。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        g = _read_hb_gen(r)
        if g is not None:
            last = g
            if g >= expected:
                return g
        time.sleep(2)
    return last


def _has_running_tasks() -> bool:
    from src.data_platform.db import get_conn
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT 1 FROM live_task WHERE status='running' LIMIT 1")
            return cur.fetchone() is not None
    except Exception as e:
        print(f"⚠ 查 live_task 失败（fail-open 放行）: {e}", file=sys.stderr)
        return False


def _set_intent(r, target: str) -> int:
    """单 Lua：GET gen 快照 → SET intent={snapshot, target} EX 300，返回 snapshot。"""
    try:
        snapshot = int(r.eval(_SET_INTENT_LUA, 2, GEN_KEY, INTENT_KEY, target, INTENT_TTL))
    except Exception as e:
        print(f"✗ SET intent 失败: {e}", file=sys.stderr)
        sys.exit(3)
    return snapshot


def _wait_lease_empty(r, timeout: float) -> bool:
    """轮询 lease 空（≤2s 间隔）：A 让位 CAS DEL 或 A 崩 30s TTL 过期。

    读失败 fail-closed（继续等），不误判「lease 空」提前启动目标。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = r.get(LEASE_KEY)
        except Exception:
            time.sleep(2)   # 读失败：继续等，不误判空
            continue
        if raw is None:
            return True
        time.sleep(2)
    return False


def _wait_takeover(r, snapshot: int, target: str, timeout: float) -> bool:
    """轮询 gen > snapshot 且 active_instance == target（guarded 原子绑定的等强度代理）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        gen = _get(r, GEN_KEY)
        active = _get(r, ACTIVE_INSTANCE_KEY)
        if gen and int(gen) > snapshot and active == target:
            return True
        time.sleep(2)
    return False


def _start_target(target: str) -> None:
    unit = f"quant-md-hub@{target}"
    print(f"启动目标 {unit}（经 quant-svc）…")
    try:
        subprocess.run([QUANT_SVC, "start", unit], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"✗ start {unit} 失败: {e.stderr or e}", file=sys.stderr)
        sys.exit(4)
    except FileNotFoundError:
        print(f"✗ 找不到 {QUANT_SVC}（须在服务器上运行）", file=sys.stderr)
        sys.exit(4)


def _finalize(r, target: str, snapshot: int) -> None:
    """收尾：校验 active_instance==target → DEL intent → 审计。"""
    active = _get(r, ACTIVE_INSTANCE_KEY)
    if active != target:
        print(f"⚠ active_instance={active} ≠ target={target}（疑似静默回退，人工介入）", file=sys.stderr)
    try:
        r.delete(INTENT_KEY)
    except Exception as e:
        print(f"⚠ DEL intent 失败: {e}", file=sys.stderr)
    try:
        from src.data_platform.audit import audit_log
        audit_log("switch.py", "hub_source_switch", target=target,
                  detail=f"gen {snapshot}→{snapshot+1}", new_value=target)
    except Exception as e:
        print(f"⚠ 审计写失败: {e}", file=sys.stderr)


def cmd_confirm(target: str) -> int:
    # (a) 切前闸
    if _has_running_tasks():
        print("✗ 有 running 任务，拒切（退出码 2）", file=sys.stderr)
        return 2
    # (b) 校验 target
    if target not in VALID_TARGETS:
        print(f"✗ target 非法（须 quant/quant2）: {target}（退出码 5）", file=sys.stderr)
        return 5
    r = _make_valkey()
    # (c) 单 Lua SET intent
    snapshot = _set_intent(r, target)
    print(f"已设切换意图 intent={{snapshot:{snapshot}, target:{target}}}，等现任让位…")
    # (d) 等让位（≤2s 轮询，60s 超时）
    if not _wait_lease_empty(r, 60):
        print("✗ 等现任让位超时（60s），需人工冷切（退出码 3）", file=sys.stderr)
        return 3
    # (e) 启动目标
    _start_target(target)
    # (f) 等接管（gen>snapshot 且 active_instance==target，30s 超时）
    if not _wait_takeover(r, snapshot, target, 30):
        print("✗ 等目标接管超时（30s，退出码 4）", file=sys.stderr)
        return 4
    # (g) 数据流证据（心跳 gen==snapshot+1，防假接管；轮询，B 心跳 ~5-15s 后首跳）
    hb_gen = _wait_hb_gen(r, snapshot + 1, 20)
    if hb_gen is None or hb_gen != snapshot + 1:
        print(f"⚠ 心跳 gen={hb_gen} 未达 snapshot+1={snapshot+1}（疑似假接管，待次日验证）", file=sys.stderr)
    # (h) 收尾
    _finalize(r, target, snapshot)
    print(f"✓ 切到 {target}，gen {snapshot}→{snapshot+1}")
    return 0


def cmd_check() -> int:
    r = _make_valkey()
    intent = _read_intent(r)
    gen = _get(r, GEN_KEY)
    lease = _get(r, LEASE_KEY)
    active = _get(r, ACTIVE_INSTANCE_KEY)
    hb_gen = _read_hb_gen(r)
    print(f"intent={intent}  gen={gen}  lease={lease}  active_instance={active}  心跳gen={hb_gen}")
    if intent is None and active:
        print(f"稳态：现任={active}")
    elif intent is not None:
        print(f"切换中：目标={intent.get('target')} 快照={intent.get('snapshot')}")
    else:
        print("无 intent 且无 active_instance（首次部署/冷环境）")
    return 0


def cmd_abort() -> int:
    r = _make_valkey()
    intent = _read_intent(r)
    if intent is None:
        print("无 intent，无需 abort")
        return 0
    active = _get(r, ACTIVE_INSTANCE_KEY)
    if active == intent.get("target"):
        print("✗ 目标已接管（active_instance==target），不能 abort，须走完收尾", file=sys.stderr)
        return 4
    try:
        r.delete(INTENT_KEY)
    except Exception as e:
        print(f"✗ DEL intent 失败: {e}", file=sys.stderr)
        return 3
    print("已中止切换（DEL intent）")
    return 0


def main() -> int:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)   # M5：confirm 阻塞驻留，忽略 SIGHUP 防关终端杀（下沉主函数，避免导入副作用）
    args = sys.argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        return 5
    cmd = args[0]
    if cmd == "confirm":
        if len(args) != 2:
            print("用法: switch.py confirm <quant|quant2>", file=sys.stderr)
            return 5
        return cmd_confirm(args[1])
    if cmd == "check":
        return cmd_check()
    if cmd == "abort":
        return cmd_abort()
    print(f"✗ 未知子命令: {cmd}", file=sys.stderr)
    return 5


if __name__ == "__main__":
    sys.exit(main())
