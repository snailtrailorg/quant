#!/usr/bin/env python
"""P3 插件起停验证：切 impl=plugin → 装配端口 → 租约续租 → 释放 → 复原。

用法（server/ 目录）：venv/bin/python ../scripts/verify_p3_plugin_lifecycle.py [account_id]
退出码：0=通过；1=失败；2=环境不满足（无凭证/窗口开/无账号）。
"""
from __future__ import annotations

import sys
import time


class _EE:
    """最小事件引擎替身（插件只调用 register）。"""

    def __init__(self):
        self.handlers = {}
        self.event_engine = self

    def register(self, t, h):
        self.handlers.setdefault(t, []).append(h)


def main() -> int:
    from src.data_platform.db import get_conn
    from src.strategy_runner import td_registry
    from src.strategy_runner.session_slot import SessionSlotAllocator

    with get_conn() as c:
        row = (c.execute("SELECT id, provider, impl FROM trading_account WHERE provider='xtp' "
                         "AND enabled ORDER BY id LIMIT 1").fetchone())
    if row is None:
        print("SKIP: 无 enabled 的 xtp 账号")
        return 2
    aid, _provider, prev_impl = row
    print(f"account={aid} prev_impl={prev_impl}")

    with get_conn() as c:
        c.execute("UPDATE trading_account SET impl='plugin' WHERE id=%s", (aid,))
        c.commit()

    ok = False
    slot = None
    try:
        ee = _EE()
        rt = td_registry.build_td_runtime("xtp", ee, 1, aid, int(time.time()))
        assert rt and rt.get("exec_port"), "运行时缺 exec_port"
        slot = rt.get("session_slot")
        assert slot and slot.terminal_id == 2, f"terminal 号异常: {slot}"
        print(f"build OK: terminal_id={slot.terminal_id} prefix={slot.order_prefix} "
              f"window_open={slot.window_open} td_open={rt['td_open']}")
        assert rt["adapter"].close_action(type("P", (), {"direction": "direction_long"})()) == "SELL"

        with get_conn() as c:
            got = c.execute("SELECT holder FROM provider_session_slot WHERE account_id=%s "
                            "AND provider='xtp' AND terminal_id=%s", (aid, slot.terminal_id)).fetchone()
        assert got and got[0] == slot.holder, "租约行缺失"
        print(f"lease OK: holder={slot.holder}")

        SessionSlotAllocator.renew(slot)
        time.sleep(1)
        SessionSlotAllocator.renew(slot)
        print("renew OK x2")
        ok = True
    except SystemExit as e:
        print(f"FAIL: SystemExit({e.code})（75=有主/DB 不可用；78=号段配置非法）")
    except Exception as e:   # noqa: BLE001
        print(f"FAIL: {type(e).__name__}: {e}")
    finally:
        if slot is not None:
            try:
                SessionSlotAllocator.release(slot)
            except Exception:   # noqa: BLE001
                pass
        with get_conn() as c:
            n = c.execute("SELECT count(*) FROM provider_session_slot WHERE account_id=%s", (aid,)).fetchone()[0]
            c.execute("UPDATE trading_account SET impl=%s WHERE id=%s", (prev_impl, aid))
            c.commit()
        print(f"cleanup: slot_rows={n} impl_restored={prev_impl}")
        ok = ok and n == 0

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
