"""批 65a（D26-A）：TD provider 插件注册表测试。

守门：注册机制/键域（PROVIDER_MARKET 锚——Broker._REGISTRY 无 emt_emq 不可锚）/builder
可调性（八键形状）/分发等价性（None→stub 可达、dict→八键消费）。
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock, patch

from src.strategy_runner import td_registry
from src.strategy_runner.td_registry import TD_BUILDERS, build_td_runtime, register_td_builder


def test_register_mechanism():
    """decorator 注册/重复注册 AssertionError/未注册返 None（注入后自清——模块级 dict 可变）。"""
    try:
        @register_td_builder("__test_probe__")
        def _probe(ee, tid, account_id, boot_epoch):
            return {k: None for k in ("gw", "td_api", "adapter", "setting",
                                      "td_open", "lead", "lag", "cfg_adapter")}
        assert "__test_probe__" in TD_BUILDERS
        assert build_td_runtime("__test_probe__", None, 1, 2, 3) is not None  # 合法八键透传
        try:
            @register_td_builder("__test_probe__")
            def _dup(ee, tid, account_id, boot_epoch):
                return {}
            raise AssertionError("重复注册应炸")
        except RuntimeError as e:
            assert "重复注册" in str(e)
        assert build_td_runtime("__unregistered__", None, 1, 2, 3) is None
    finally:
        TD_BUILDERS.pop("__test_probe__", None)


def test_key_domain_providers():
    """键域守门：TD_BUILDERS ⊆ PROVIDER_MARKET（配置域真源含 emt_emq——批 63 P4 无地雷）。

    不锚 Broker._REGISTRY：该表无 emt_emq（broker.py:68-72）且两角色非 1:1
    （crypto 有 Broker 无 TD builder 反例）。
    """
    from src.quant_common.markets import PROVIDER_MARKET
    assert set(TD_BUILDERS) <= set(PROVIDER_MARKET), \
        f"TD builder 键越配置域: {set(TD_BUILDERS) - set(PROVIDER_MARKET)}"
    assert "xtp" in TD_BUILDERS


def test_xtp_builder_shape():
    """builder 可调性：mock 全外部依赖（td_registry 命名空间的模块级 import+md_session 三函数
    ——不 patch 则真连 DB+时钟 flaky），断言八键形状与 td_open 分支。"""
    fake_setting = {"账号": "a", "密码": "b", "客户号": "1", "交易地址": "x", "交易端口": "1"}
    # patch 值非默认（7,3≠产默认 10/10）——若 patch 机制失效走真 DB fail-open 默认则断言必红（防假绿）
    with patch.object(td_registry, "build_xtp_setting", return_value=fake_setting), \
         patch("src.strategy_framework.md_session.is_trading_day", return_value=True), \
         patch("src.strategy_framework.md_session.load_xtp_window_cfg", return_value=(7, 3)), \
         patch("src.strategy_framework.md_session.xtp_session_window_open", return_value=False), \
         patch("vnpy_xtp.gateway.xtp_gateway.XtpTdApi") as api_cls, \
         patch("src.strategy_framework.adapters.XTPAdapter") as ad_cls:
        rt = build_td_runtime("xtp", MagicMock(), 7, 4, 99)
        assert set(rt) == {"gw", "td_api", "adapter", "setting", "td_open",
                           "lead", "lag", "cfg_adapter"}   # 八键契约（与 stub 分支键集一致）
        assert rt["setting"] is fake_setting
        assert rt["td_open"] is False and rt["lead"] == 7 and rt["lag"] == 3
        assert rt["cfg_adapter"] == "xtp"
        assert rt["gw"].td_api is api_cls.return_value
        api_cls.return_value.connect.assert_not_called()   # 窗关不连
        ad_cls.assert_called_once()   # order_prefix 含 tid/boot_epoch
        assert "t7:e99:" in ad_cls.call_args.kwargs["order_prefix"]


def test_xtp_builder_connects_when_window_open():
    """td_open=True 分支钉：gw.connect(setting)→td_api.connect 位置参数转发（TD 会话建立面）。"""
    fake_setting = {"账号": "acct", "密码": "pw", "客户号": "42", "交易地址": "tcp://x",
                    "交易端口": "1", "授权码": "ok"}
    with patch.object(td_registry, "build_xtp_setting", return_value=fake_setting), \
         patch("src.strategy_framework.md_session.is_trading_day", return_value=True), \
         patch("src.strategy_framework.md_session.load_xtp_window_cfg", return_value=(7, 3)), \
         patch("src.strategy_framework.md_session.xtp_session_window_open", return_value=True), \
         patch("vnpy_xtp.gateway.xtp_gateway.XtpTdApi") as api_cls, \
         patch("src.strategy_framework.adapters.XTPAdapter"):
        rt = build_td_runtime("xtp", MagicMock(), 7, 4, 99)
        assert rt["td_open"] is True
        api_cls.return_value.connect.assert_called_once_with(
            "acct", "pw", 42, "tcp://x", 1, "ok", 3)   # ThinTdGateway.connect 转发契约


def test_dispatch_none_and_dict_equivalence():
    """分发等价性：未注册→None（stub 硬闸分支可达）；注册→builder 结果透传（八键消费）。"""
    assert build_td_runtime("__nope__", None, 1, 1, 1) is None
    sentinel = {"gw": object(), "td_api": None, "adapter": object(), "setting": None,
                "td_open": True, "lead": None, "lag": None, "cfg_adapter": "x"}
    try:
        TD_BUILDERS["__eq_probe__"] = lambda ee, tid, a, b: dict(sentinel)
        assert build_td_runtime("__eq_probe__", None, 1, 1, 1) == sentinel
    finally:
        TD_BUILDERS.pop("__eq_probe__", None)


def test_main_reexport_compatible():
    """兼容 re-export：main._TD_BUILDERS 与 td_registry.TD_BUILDERS 同对象（存量断言可过）。"""
    from src.strategy_runner import main
    assert main._TD_BUILDERS is TD_BUILDERS
    assert "xtp" in main._TD_BUILDERS


def test_builder_contract_defense():
    """已注册 builder 返 None/缺键=raise（防批 63 P4 第二实例 bug 静默落 stub 装死）。"""
    try:
        TD_BUILDERS["__none_probe__"] = lambda ee, tid, a, b: None
        try:
            build_td_runtime("__none_probe__", None, 1, 1, 1)
            raise AssertionError("返 None 应炸")
        except RuntimeError as e:
            assert "None" in str(e)
        TD_BUILDERS["__keys_probe__"] = lambda ee, tid, a, b: {"gw": 1}
        try:
            build_td_runtime("__keys_probe__", None, 1, 1, 1)
            raise AssertionError("缺键应炸")
        except RuntimeError as e:
            assert "缺键" in str(e)
    finally:
        TD_BUILDERS.pop("__none_probe__", None)
        TD_BUILDERS.pop("__keys_probe__", None)
