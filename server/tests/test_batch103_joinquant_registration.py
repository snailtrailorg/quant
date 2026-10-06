"""批 103：聚宽注册 —— 第一步（凭证模板）+ 批 103b（真接，能力声明反转）。

批 103 第一步只注册配置面（interfaces 凭证模板 → UI 出现聚宽表单），**不声明能力**——
能力声明的门＝真实现（批 83b/99 立法）。批 103b 真接后（`JoinQuantAdapter` 实现
`pull_daily`/`to_bar_rows` + `capabilities={"astock_daily_jq"}`），按当时的约定
**在此反转反谎报钉**：`provider_capabilities("joinquant")` 由空集变为非空。

**新增反谎报钉（批 103b，威廉姆 2026-10-06 裁定）**：`capability_decls` **不得声明
`adj_factor`**——聚宽因子归一化锚点与 tushare 不同（比值逐标的恒定，见任务文件 §0.4），
本仓的因子真源是 tushare 独有通道 `backfill_adj_factor`；声明该能力＝谎报。
"""
import src.data_platform.interfaces.joinquant  # noqa: F401  # 注册副作用
from src.data_platform.adapters.base import _ADAPTERS
from src.data_platform.capabilities import provider_capabilities
from src.data_platform.interfaces import base as iface_base
from src.quant_common.markets import DOMAIN_CAPS


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

    def test_capability_declared_after_real_implementation(self):
        """**批 103b 反转**：真接后能力必须非空且 ⊆ 数据源域（原反谎报钉＝空集）。

        反证：若有人回退 `JoinQuantAdapter.capabilities`（撤回真接）而不改本测试，
        本条红；若有人给空能力 stub 声明能力，本条也会因域不符而红。
        """
        cls = _ADAPTERS.get("joinquant")
        assert cls is not None and cls.__name__ == "JoinQuantAdapter"
        caps = provider_capabilities("joinquant")
        assert caps == {"hist_quote"}, caps
        assert caps <= set(DOMAIN_CAPS["data_source"]), (
            f"聚宽能力越出数据源域：{sorted(caps - set(DOMAIN_CAPS['data_source']))}")

    def test_astock_daily_jq_is_declared(self):
        """真接落点：`astock_daily_jq` 为**独立 sync_id**（不占 `astock_daily` 切换位）。"""
        cls = _ADAPTERS["joinquant"]
        assert "astock_daily_jq" in set(cls.capabilities)
        assert "astock_daily" not in set(cls.capabilities), (
            "聚宽不得声明 astock_daily——试用窗口无最近 3 个月，会静默造成数据倒退")

    def test_no_adj_factor_capability_claim(self):
        """**反谎报钉（批 103b 新增）**：聚宽**不得**声明 `adj_factor` 能力。

        原因（威廉姆 2026-10-06）：聚宽 `factor` 与 tushare `adj_factor` 是同一复权序列的
        不同归一化锚点（比值逐标的恒定），不可互换；本仓因子真源＝tushare 独有通道
        `backfill_adj_factor`。聚宽只提供 OHLCV，`to_bar_rows` 写 `adj_factor=None`。
        """
        kinds = {(d.kind, d.temporality) for d in _ADAPTERS["joinquant"].capability_decls}
        assert ("adj_factor", "historical") not in kinds, (
            "聚宽不得声明 adj_factor 能力（因子归 tushare 独有通道，声明即谎报）")
        assert ("bar_daily", "historical") in kinds   # 正向：行情能力必须声明
