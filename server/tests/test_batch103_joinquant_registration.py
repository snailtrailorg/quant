"""批 103 第一步：聚宽凭证模板注册（威廉姆 2026-10-06，测试账号已申请）。

只注册**配置面**（interfaces 凭证模板 → UI 凭证页出现聚宽表单，凭证由威廉姆设置），
不声明能力、不动 adapter stub。能力声明的门＝真实现（批 83b/99 立法）——stub 期
声明能力＝前端把 `astock_daily` 判为「可切聚宽」，真切过去当场 NotImplementedError
（「清单有·实现无」家族）。真接批实现 pull 后反转 `test_stub_capability_honesty`。
"""
import src.data_platform.interfaces.joinquant  # noqa: F401  # 注册副作用
from src.data_platform.adapters.base import _ADAPTERS
from src.data_platform.capabilities import provider_capabilities
from src.data_platform.interfaces import base as iface_base


class TestJoinQuantRegistration:
    def test_provider_registered(self):
        inst = iface_base.get_interface_provider("joinquant")
        assert inst is not None
        assert inst.market == "astock"
        assert inst.provider == "joinquant"

    def test_module_listed_for_bootstrap(self):
        """进 _PROVIDER_MODULES 才会被 bootstrap 遍历注册（原注释「聚宽不入表」已随本批推翻）。"""
        assert "joinquant" in iface_base._PROVIDER_MODULES

    def test_field_schema_shape(self):
        inst = iface_base.get_interface_provider("joinquant")
        keys = [f["key"] for f in inst.FIELD_SCHEMA]
        assert keys == ["account", "password"]
        secret = {f["key"]: bool(f.get("secret")) for f in inst.FIELD_SCHEMA}
        assert secret == {"account": False, "password": True}

    def test_credentials_completeness_requires_both(self):
        """凭证完整性校验（批 63 二增强）覆盖账号与密码两者。"""
        inst = iface_base.get_interface_provider("joinquant")
        assert inst.required_fields == {"account", "password"}

    def test_stub_capability_honesty(self):
        """反谎报钉：stub 期 provider_capabilities 必须空集（真切批实现后随能力声明反转）。"""
        assert provider_capabilities("joinquant") == set()
        cls = _ADAPTERS.get("joinquant")
        assert cls is not None and cls.__name__ == "JoinQuantAdapter"
