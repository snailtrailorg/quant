"""批55-0:市场/交易所/品类/能力 维度注册表(全系统单源——批33b perm_registry 同模式)。

概念立法(2026-09-19 用户终裁方案一 v2):
- 行的边界=账号(连接身份);列的语义=能力;页签=能力过滤视图
- 能力真源=代码(adapter.capabilities);配置列恒为「用户启用子集」(写侧校验 ⊆)
- 词表两层:代码层=sync_id 复合串(astock_daily——品类×数据品类×粒度);
  配置层=能力枚举(daily/minute/...)——映射层 CAPABILITY_MAP 两层互通(复审发现的词表断裂解)
"""
from __future__ import annotations

MARKETS: dict[str, dict] = {
    "astock": {"name_zh": "A股", "timezone": "+08:00"},
    "crypto": {"name_zh": "加密", "timezone": "UTC"},
}

EXCHANGES: dict[str, dict] = {
    "SHSE": {"market": "astock"}, "SZSE": {"market": "astock"}, "BSE": {"market": "astock"},
    "BINANCE": {"market": "crypto"}, "OKX": {"market": "crypto"},
}

ASSET_CATEGORIES = ("stock", "etf", "convertible", "fund", "reits", "perp")
DATA_CATEGORIES = ("daily", "minute", "snapshot", "quote")
CAPABILITIES = ("daily", "minute", "snapshot", "quote", "trading")   # 配置层能力枚举(受代码消费驱动增长)

# 代码层 sync_id ↔ 配置层能力枚举 映射(tushare.capabilities 实证词表)
SYNC_ID_CAP_MAP: dict[str, str] = {
    "astock_daily": "daily", "etf_daily": "daily", "cb_daily": "daily",
    "astock_minute": "minute", "astock_minute_5min": "minute",
}
NON_DATA_PROVIDERS = {"tencent": {"minute", "snapshot"}, "xtp": {"trading", "quote"},
                      "binance_perp": {"trading"}, "okx_perp": {"trading"}}   # 无 adapter 的通道能力(快照/hub 网关层)
# 键=Broker._REGISTRY/同步路由的真实 provider 串（binance_perp/okx_perp 非 binance/okx——
# 55a 修键：与 26 号收尾批 C 对齐，词表键错位会让 ⊆ 校验误拒合法配置）

# provider → 市场（固定归属；55a 写侧校验 market 与 provider 一致，防跨市场错行）
PROVIDER_MARKET: dict[str, str] = {
    "tushare": "astock", "joinquant": "astock", "ricequant": "astock", "tencent": "astock",
    "xtp": "astock", "binance_perp": "crypto", "okx_perp": "crypto",
}

# 权限五键 → (market, category, venue) 无损映射(market_op 三分混一的立法化解)
MARKET_OP_DECOMP: dict[str, tuple] = {
    "astock": ("astock", "stock", None), "etf": ("astock", "etf", None),
    "convertible": ("astock", "convertible", None),
    "binance_perp": ("crypto", "perp", "BINANCE"), "okx_perp": ("crypto", "perp", "OKX"),
}


# 能力查询/校验在 data_platform/capabilities.py(层 0 禁 import 上层——test_layering 铁律;
# 词表与映射是纯数据留此处,消费逻辑在数据层)
