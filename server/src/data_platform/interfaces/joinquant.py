"""JoinQuant Provider（批 103 第一步）——数据拉取凭证（jqdatasdk 账号/密码）。

威廉姆 2026-10-06：聚宽测试账号已申请，凭证由他经 UI 凭证页设置。本模块只注册
**配置模板**（credentials 表单来源），不实现拉取、不声明能力。

能力纪律（勿反转）：`adapters.base.JoinQuantAdapter` 仍是 stub（三方法全
NotImplementedError），`provider_capabilities('joinquant')` 须保持**空集**——
能力声明的门＝真实现（批 83b/99 立法）；stub 期声明能力＝前端把 `astock_daily`
判为「可切聚宽」（≥2 源声明即「可切换」），运维真切过去当场 NotImplementedError。
真接批（后续）实现 pull_daily 等之后，才随 capability_decls 一起声明并反转
`test_batch103_joinquant_registration.py` 的反谎报钉。

真接批要点（承 adapters/base.py stub 注释 + rate_limit 已备）：
- 依赖：jqdatasdk（新依赖，进 requirements）
- 字段映射：聚宽 volume(股)/money(元)；fq='pre' 前复权 → 须 pin fq=None 对齐
  「未复权价 + adj_factor」统一契约
- 符号归一：聚宽 000001.XSHE ↔ 内部 ts_code 000001.SZ（`to_source_symbol` 覆写）
- 限频：DailyQuotaPolicy（每日额度，试用账号额度以实测为准）
"""
from .base import InterfaceProvider, register_provider


class JoinQuantProvider(InterfaceProvider):
    provider = "joinquant"
    market = "astock"

    FIELD_SCHEMA = [
        {"key": "account", "type": "text", "label_key": "interfaces.field.jqAccount",
         "secret": False},
        {"key": "password", "type": "text", "label_key": "interfaces.field.jqPassword",
         "secret": True},
    ]

    PARAMS_SCHEMA = []


register_provider(JoinQuantProvider())
