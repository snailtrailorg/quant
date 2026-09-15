"""批26-7：ONBOARDING_METHODS 整表重声明闸（批11D 盲审余项收口）。

类属性 dict 跨子类共享——子类必须新字面量整表重声明；__init_subclass__ 把约定变机制
（定义期 TypeError，非运行期静默错派生）。"""
import pytest


def test_missing_redeclaration_raises():
    from src.im_bot.base import IMBotProvider
    with pytest.raises(TypeError, match="整表重声明"):
        class BadProvider(IMBotProvider):   # 类体执行即触发 __init_subclass__
            provider = "bad"


def test_redeclared_subclass_ok():
    from src.im_bot.base import IMBotProvider

    class GoodProvider(IMBotProvider):
        provider = "good"
        ONBOARDING_METHODS = {"form": {"kind": "manual", "label_key": "x"}}
        # 抽象方法 stub（本测焦点=闸不炸+派生正确，非通道实现）
        send_text = send_card = test_connection = staticmethod(lambda *a, **k: None)

    assert GoodProvider().ONBOARDING == "manual"


def test_three_real_providers_pass_gate():
    """三真平台（feishu/dingtalk/wecom）均整表重声明——模块加载（类定义期）不炸。"""
    from src.im_bot.base import get_im_provider
    for name in ("feishu", "dingtalk", "wecom"):
        assert get_im_provider(name) is not None, f"{name} 应可加载"
