"""S3 守门：可插拔（注册即生效 / 删包即失效 / 能力协商）。

D32 §9.6 的验收定义：新增 provider = 加目录 + 一行注册 + 一次发布，核心零改动。
本测试验证注册表合约与「import 即注册」模式；真实 provider 由 P3/P4/P5 验证。
"""

from __future__ import annotations

import importlib
import sys
import textwrap

import pytest

from src.registry import (
    DOMAIN_DATA,
    DOMAIN_RESEARCH,
    DOMAIN_TRADING,
    PORT_CAPS,
    UnsupportedCapability,
    UnknownProvider,
    clear,
    get,
    pick,
    register,
    registered,
)


class _Plugin:
    def __init__(self, port_caps: set[str]):
        self.manifest = {"port_caps": port_caps}

    def build(self, account, role, slot):  # pragma: no cover - 合约占位
        return object()


@pytest.fixture(autouse=True)
def _clean():
    clear()
    yield
    clear()


def test_register_and_get():
    p = _Plugin({"execution"})
    register(DOMAIN_TRADING, "xtp", p)
    assert get(DOMAIN_TRADING, "xtp") is p
    assert (DOMAIN_TRADING, "xtp") in registered()


def test_domains_are_three():
    assert {DOMAIN_DATA, DOMAIN_TRADING, DOMAIN_RESEARCH} == {
        "data_source", "trading_account", "research",
    }
    assert PORT_CAPS == {"execution", "market_data", "research", "history", "account"}


def test_pick_by_capability():
    p = _Plugin({"execution", "market_data"})
    register(DOMAIN_TRADING, "xtp", p)
    assert pick(DOMAIN_TRADING, "xtp", {"execution"}, "plugin") is p


def test_pick_rejects_missing_capability():
    register(DOMAIN_TRADING, "xtp", _Plugin({"execution"}))
    with pytest.raises(UnsupportedCapability):
        pick(DOMAIN_TRADING, "xtp", {"market_data"}, "plugin")


def test_unknown_provider():
    with pytest.raises(UnknownProvider):
        get(DOMAIN_TRADING, "nope")


def test_duplicate_registration_rejected():
    register(DOMAIN_TRADING, "xtp", _Plugin({"execution"}))
    with pytest.raises(ValueError):
        register(DOMAIN_TRADING, "xtp", _Plugin({"execution"}))


def test_invalid_domain_rejected():
    with pytest.raises(ValueError):
        register("nonsense", "x", _Plugin({"execution"}))


def test_same_provider_two_domains_are_independent():
    """`binance`（数据源）与 `binance_perp`（交易通道）是两把键，互不覆盖。"""
    register(DOMAIN_DATA, "binance", _Plugin({"history"}))
    register(DOMAIN_TRADING, "binance_perp", _Plugin({"execution"}))
    assert get(DOMAIN_DATA, "binance") is not get(DOMAIN_TRADING, "binance_perp")
    with pytest.raises(UnknownProvider):
        get(DOMAIN_TRADING, "binance")


def test_registry_view_is_read_only():
    from src.registry import REGISTRY

    register(DOMAIN_TRADING, "xtp", _Plugin({"execution"}))
    assert (DOMAIN_TRADING, "xtp") in REGISTRY
    with pytest.raises(TypeError):
        REGISTRY[(DOMAIN_TRADING, "other")] = object()   # type: ignore[index]


def test_manifest_must_match_registration_args():
    class _BadId:
        manifest = {"id": "other", "port_caps": {"execution"}}
        def build(self, account, role, slot):
            return object()

    with pytest.raises(ValueError):
        register(DOMAIN_TRADING, "xtp", _BadId())

    class _BadDomain:
        manifest = {"domain": DOMAIN_RESEARCH, "port_caps": {"execution"}}
        def build(self, account, role, slot):
            return object()

    with pytest.raises(ValueError):
        register(DOMAIN_TRADING, "xtp", _BadDomain())


def test_legacy_projection_marked_and_cleared():
    from src.registry import legacy_registered

    register(DOMAIN_TRADING, "xtp_legacy", _Plugin({"execution"}), legacy_only=True)
    assert (DOMAIN_TRADING, "xtp_legacy") in legacy_registered()
    clear()
    assert legacy_registered() == []


def test_invalid_provider_rejected():
    for bad in [None, 123, "", " xtp", "xtp "]:
        with pytest.raises(ValueError):
            register(DOMAIN_TRADING, bad, _Plugin({"execution"}))


def test_missing_build_rejected():
    class _NoBuild:
        manifest = {"port_caps": {"execution"}}

    with pytest.raises(ValueError):
        register(DOMAIN_TRADING, "nobuild", _NoBuild())


def test_unknown_port_cap_rejected():
    with pytest.raises(ValueError):
        register(DOMAIN_TRADING, "bogus", _Plugin({"execution", "teleport"}))


def test_import_registers_and_removal_invalidates(tmp_path, monkeypatch):
    """「import 即注册」；注册表生命周期=进程，清空后不可再取。

    说明：真实「删包」不会让活进程的注册表消失（需重启），这里用 clear() 模拟
    进程内失效，验证的是「取用只认注册表、没有隐式兜底」。
    """
    pkg = tmp_path / "fake_provider_172a"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(textwrap.dedent("""
        from src.registry import DOMAIN_TRADING, register

        class _P:
            manifest = {"port_caps": {"execution"}}
            def build(self, account, role, slot):
                return object()

        register(DOMAIN_TRADING, "fake", _P())
    """), encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.import_module("fake_provider_172a")
    assert get(DOMAIN_TRADING, "fake") is not None

    # 删包（模拟包被移除）：注册表清空后不可再取
    clear()
    sys.modules.pop("fake_provider_172a", None)
    with pytest.raises(UnknownProvider):
        get(DOMAIN_TRADING, "fake")
