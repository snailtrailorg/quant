"""JoinQuant Provider —— 数据拉取凭证（jqdatasdk 账号/密码）。

本模块只注册 **配置模板**（credentials 表单来源＝`FIELD_SCHEMA`），不实现拉取。
真实现与能力声明在 `adapters/joinquant_adapter.py` + `data_source.JoinQuantDataSource`。

**状态（2026-10-06 批 103b 真接后）**：`provider_capabilities('joinquant')` ＝
`{"hist_quote"}`（由 `astock_daily_jq` 经 `SYNC_ID_CAP_MAP` 归一），`provider_domain`
＝`data_source` ⇒ **凭证页「数据源」页签的新增下拉会出现「聚宽」**，威廉姆可自行填
account/password。（批 103 期该 provider 能力集为空，两道目录门全跳 ⇒ 界面不可见；
这是「stub 期注册模板」在现有立法下的必然结果，非缺陷。）

**能力纪律（勿回退）**：能力声明的门＝真实现（批 83b/99 立法）。若撤回真接实现，
必须**同时**撤回 `capabilities` 并反转 `test_batch103_joinquant_registration.py` 的
证钉——否则前端把 `astock_daily_jq` 判为「可切聚宽」而真切当场失败。

**已知边界**（试用账号，见 `flow/任务/批103b-聚宽真接.md`）：
- 窗口＝**绝对区间**（实测 2025-06-28~2026-07-05），动态取自 `get_account_info()`；
  **不含最近 3 个月** ⇒ 聚宽是**历史切片补充源**，不能替代 `astock_daily`。
- 每日额度 100 万条（按**返回行数**计）；连接数=1 ⇒ provider 级互斥。
- 复权因子跨源基准不同 ⇒ 不声明 `adj_factor` 能力、不写因子（由 tushare 回填通道补）。
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
