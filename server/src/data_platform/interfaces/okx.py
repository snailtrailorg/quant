"""OKX 公开数据源 Provider（批 102b）——**0 密钥**（`www.okx.com/api/v5/market/*` 公共行情）。

与 `okx_perp.py` 的区别（**两把键，勿混**）：
- `okx_perp` ＝**交易通道**（批 63 凭证桩：api_key/api_secret/passphrase，下单/实时）；
- `okx`      ＝**数据源**（本类，公共历史行情本就无鉴权）⇒ **两处 schema 皆空**。

混用会让 `_get_supply_adapter` 找不到 adapter 而**静默回落 tushare 拉错源**——与
`binance` / `binance_perp` 同一族教训（批 101 已立钉）。

空 schema ≠ 漏配置：`mgmt._normalize_credentials` 对无 schema provider 直接跳过校验
（`required_fields` 为空集 ⇒ 无必填字段），前端表单渲染为零字段——正是「此源不需要任何
凭证」的正确表达（`provider = "okx"` 才进数据源页签下拉）。

⚠️ **数据源域「零字段」不等于「零配置」**：本源在 prod 上**必须经代理出口**才可达
（2026-10-06 实测：prod 直连 `www.okx.com` 被解析到 169.254.0.2 而不可用）——
那部分配置在集成中心「代理出口」页（102a 的 `proxy_binding.consumer='okx'`），
**不在此 schema**（0 密钥是本源的凭证面事实，代理是出口面配置，两维独立）。
"""
from .base import InterfaceProvider, register_provider


class OkxProvider(InterfaceProvider):
    provider = "okx"
    market = "crypto"

    FIELD_SCHEMA: list[dict] = []     # 公开历史行情，无凭证
    PARAMS_SCHEMA: list[dict] = []    # 无连接参数（基址在 adapter 内固定，可经 proxy_binding 覆盖）


register_provider(OkxProvider())
