"""币安公开数据源 Provider（批 101）——**0 密钥**（`data.binance.vision` 批量历史）。

与 `binance_perp.py` 的区别：`binance_perp` = 交易通道（api_key/api_secret 下单/实时）；
`binance` = 数据源（公开批量行情端点本就无鉴权）⇒ **两处 schema 皆空**。

空 schema ≠ 漏配置：`mgmt._normalize_credentials` 对无 schema provider 直接跳过校验
（`inst.required_fields` 为空集 ⇒ 无必填字段），前端表单渲染为零字段——
正是「此源不需要任何凭证」的正确表达（`provider = "binance"` 才进数据源页签下拉）。
"""
from .base import InterfaceProvider, register_provider


class BinanceProvider(InterfaceProvider):
    provider = "binance"
    market = "crypto"

    FIELD_SCHEMA: list[dict] = []     # 公开历史行情，无凭证
    PARAMS_SCHEMA: list[dict] = []    # 无连接参数（批量站地址在 adapter 内固定）


register_provider(BinanceProvider())
