"""D32 P4 金样：EMT TD 运行时必经插件端口（形状/终端号映射/连接窗/impl 门）。

与 XTP 金样同结构（test_d32_s7_golden_master.py）：builder 是唯一组装处，
平台层只给 code/tid，provider 私有号段与连接语义在插件内。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _slot(terminal_id: int = 7, window_open: bool = False):
    from src.strategy_runner.session_slot import SessionSlot
    return SessionSlot(login_key="emt|acct", provider="emt_emq", role="td",
                       terminal_id=terminal_id, order_prefix="p", window_open=window_open,
                       tid=terminal_id, holder="h", boot_epoch=1)


def _env(monkeypatch, *, impl="plugin", window_open=False, terminal_id=7):
    """打桩：接口行/窗口/槽租约；EmtAdapter 在插件内惰导入，故打 adapters 符号。"""
    monkeypatch.setattr("src.data_platform.iface_store.get_td_identity",
                        lambda c: {"code": c, "provider": "emt_emq", "impl": impl,
                                   "login_key": "emt|acct"})
    monkeypatch.setattr("src.data_platform.iface_store.td_sibling_codes", lambda lk: ["td-emt-emq"])
    monkeypatch.setattr("src.strategy_framework.broker.get_interface_row",
                        lambda code, md_only=False: {
                            "code": code, "provider": "emt_emq", "enabled": True,
                            "credentials": {"emt_account": "acct", "emt_password": "pw"},
                            "params": {"emt_td_host": "tcp://h:19088"}})
    monkeypatch.setattr("src.providers.xtp.session.load_xtp_window_cfg", lambda: (10, 5))
    monkeypatch.setattr("src.providers.xtp.session.is_trading_day", lambda *a, **k: True)
    monkeypatch.setattr("src.providers.xtp.session.xtp_session_window_open", lambda *a, **k: window_open)
    # 模拟真实 allocator：terminal_id 由调用方传入（builder 传 code_terminal），并回填槽
    acquire = MagicMock(side_effect=lambda *a, **k: _slot(
        terminal_id=k.get("terminal_id", 2), window_open=window_open))
    slot = _slot(terminal_id=terminal_id, window_open=window_open)
    monkeypatch.setattr("src.strategy_runner.session_slot.SessionSlotAllocator.acquire", acquire)
    adapter = MagicMock()
    mk = MagicMock(return_value=adapter)
    monkeypatch.setattr("src.strategy_framework.adapters.EmtAdapter", mk)
    return acquire, mk, adapter, slot


def test_emt_runtime_goes_through_plugin_port(monkeypatch):
    """八键 + session_slot（无 exec_port）；窗口开=建连，槽按 code 分配（provider=emt_emq）。

    exec_port 缺席是契约：EmtAdapter 不是 ExecutionPort，给裸 adapter 会让
    subscribe_order/connected 直接 AttributeError；live_task 侧缺省回落 LegacyExecutionPort。
    """
    acquire, mk, adapter, slot = _env(monkeypatch, window_open=True, terminal_id=7)

    from src.strategy_runner.td_registry import build_td_runtime
    rt = build_td_runtime("emt_emq", MagicMock(), 7, "td-emt-emq", 100,
                          client_id=2, order_prefix="a-td-emt-emq:e100:")

    assert set(rt) == {"gw", "td_api", "adapter", "setting", "td_open", "lead", "lag",
                       "cfg_adapter", "session_slot"}
    assert "exec_port" not in rt          # 端口未落地 → 不得以裸 adapter 冒充
    assert rt["gw"] is rt["td_api"] is rt["adapter"] is adapter
    assert rt["session_slot"].terminal_id == 2
    assert rt["cfg_adapter"] == "emt_emq" and rt["td_open"] is True
    assert rt["setting"]["account"] == "acct" and rt["setting"]["td_port"] == 19088

    # 槽：provider 维度 + terminal_id 取 code_terminal（平台层不持私有号段）
    args, kwargs = acquire.call_args
    assert args[:4] == ("emt|acct", "emt_emq", "td", 7)
    assert kwargs["terminal_id"] == 2        # 单兄弟 → 字典序首位 → 2..99 域内
    # 插件内映射 EMT SDK 域：tcid=(slot.terminal_id-1)%100+20，槽 2..99 → 实际 21..118
    assert mk.call_args.kwargs["td_client_id"] == 21
    assert mk.call_args.kwargs["order_prefix"] == "a-td-emt-emq:e100:"
    adapter.connect.assert_called_once_with(rt["setting"])


def test_emt_runtime_window_closed_defers_login(monkeypatch):
    """窗关启动：只 remember_login，不连——窗开沿由执行进程补首连。"""
    _, _, adapter, _ = _env(monkeypatch, window_open=False)

    from src.strategy_runner.td_registry import build_td_runtime
    rt = build_td_runtime("emt_emq", MagicMock(), 7, "td-emt-emq", 100)

    assert rt["td_open"] is False
    adapter.connect.assert_not_called()
    adapter.remember_login.assert_called_once_with(rt["setting"])


def test_emt_runtime_accepts_both_impl_values(monkeypatch):
    """过渡期：plugin/legacy 都走本函数（端口未落地，impl 门留到翻转时一起开）。"""
    from src.strategy_runner.td_registry import build_td_runtime
    for impl in ("plugin", "legacy"):
        _env(monkeypatch, impl=impl)
        rt = build_td_runtime("emt_emq", MagicMock(), 7, "td-emt-emq", 100)
        assert rt["cfg_adapter"] == "emt_emq" and "exec_port" not in rt


def test_emt_runtime_rejects_unknown_impl(monkeypatch):
    _env(monkeypatch, impl="bogus")
    from src.strategy_runner.td_registry import build_td_runtime
    with pytest.raises(RuntimeError, match="非法"):
        build_td_runtime("emt_emq", MagicMock(), 7, "td-emt-emq", 100)


@pytest.mark.parametrize("slot_num,expect", [(2, 21), (7, 26), (80, 99), (81, 100), (99, 118)])
def test_emt_terminal_mapping_stays_in_sdk_domain(slot_num, expect):
    """槽号 2..99 → SDK 号 21..118 单射且不越界（声明域 20..119 的子集）。"""
    from src.providers.emt.plugin import EmtPlugin
    with patch("src.strategy_framework.adapters.EmtAdapter") as mk:
        EmtPlugin().build(None, "td", _slot(terminal_id=slot_num), event_engine=MagicMock(), setting={})
    assert mk.call_args.kwargs["td_client_id"] == expect
    assert 21 <= expect <= 118
