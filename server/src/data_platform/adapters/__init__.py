"""adapters 包——**导入即注册**（`@register_adapter` 的 import 副作用）。

批 101：`binance_adapter` 必须在此显式导入。注册表 `_ADAPTERS` 靠 import 副作用填充，
**未被导入的模块 = adapter 静默缺席**——`get_adapter("binance")` 抛 ValueError 后
`engine._get_supply_adapter` 会**回落 tushare 拉错源且不报错**（正是「清单有·实现无」那一族）。
tushare 侧无需此步：`TushareAdapter` 定义在 `base.py` 内，导入 base 即注册。

批 102b：`okx_adapter` 同理由（okx 数据源，0 密钥公共行情）。
"""
from . import binance_adapter as binance_adapter  # noqa: F401
from . import joinquant_adapter as joinquant_adapter  # noqa: F401  （批 103b 真接）
from . import okx_adapter as okx_adapter  # noqa: F401  （批 102b 真接）
