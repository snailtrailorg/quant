"""批55-0:市场/交易所/品类/能力 维度注册表(全系统单源——批33b perm_registry 同模式)。

概念立法(2026-09-19 用户终裁方案一 v2; D25 v2 2026-09-25 能力集重构):
- 行的边界=账号(连接身份);列的语义=能力集;配置页签=能力筛选(D25:页签合并为单列表)
- 能力真源=DataKind(contract.py 27 词);能力集=派生归类视图(KIND_CAP_CLASS 单源映射,
  非独立建模);配置列恒为「用户启用子集」(写侧校验 ⊆)
- 词表三层(D25):代码层 sync_id(品类×粒度复合串) → DataKind(细粒度能力) → 能力集 token(5 类)
- token 立法:trading 保词(8 处 SQL 域谓词 'trading'=ANY(capabilities) 零改动——实盘选账号链路),
  daily/minute/snapshot/quote 四旧词退役(SQL 消费唯 routing._CAP_KIND_ALIASES 一处随派生表重写)
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

# 配置层能力集 token（D25 v2 立法：5 类；中文名经文案师审校定稿——「行情」撞 cap_quote/
# 「事件」撞事件时间线/「公司事件」不适配 OKX 均已消歧为下述命名）
CAPABILITIES = ("hist_quote", "rt_quote", "trading", "ref_data", "inst_event")

# 能力集中文标签（UI 渲染；ref_data 建议 tooltip：「股票列表、交易日历、财务指标、资金流向、龙虎榜等辅助分析数据」）
CAP_CLASS_LABELS: dict[str, str] = {
    "hist_quote": "历史行情", "rt_quote": "实时行情", "trading": "交易",
    "ref_data": "参考数据", "inst_event": "标的事件",
}

# DataKind → 能力集 派生映射（D25 §四.2 显式映射表——单源；加 DataKind 必加行，
# test_markets_registry 断言 DATA_KINDS 全覆盖且互斥——31 项漏网的根因正是无此断言）
KIND_CAP_CLASS: dict[str, str] = {
    # hist_quote 历史行情（价格·批量：回测/因子/选股）
    "bar_daily": "hist_quote", "bar_minute": "hist_quote", "index_daily": "hist_quote",
    "adj_factor": "hist_quote",   # 显式挪类（contract.py 原分组=参考）：随 bar 同步拉取、BAR_COLUMNS 旁挂
    # rt_quote 实时行情（价格·实时：实盘决策）
    "snapshot": "rt_quote", "stream_bar": "rt_quote", "stream_tick": "rt_quote", "depth": "rt_quote",
    # trading 交易（操作面）
    "account_query": "trading", "order_stream": "trading", "trade_exec": "trading", "settlement": "trading",
    # ref_data 参考数据（非价格·辅助）
    "static_list": "ref_data", "trade_cal": "ref_data", "index_constituents": "ref_data",
    "industry_class": "ref_data", "fundamental_daily": "ref_data", "financial_stmt": "ref_data",
    "featured_daily": "ref_data", "stk_limit": "ref_data", "funding_rate": "ref_data",
    "holder_structure": "ref_data",
    "open_interest": "ref_data", "liquidation": "ref_data", "fx_rate": "ref_data",  # 占位归类，消费落地重裁
    # inst_event 标的事件（时点：跨市场——A股停复牌/除权强赎 + 加密下架/换币/硬分叉）
    "suspend": "inst_event", "corporate_action": "inst_event",
}

# 代码层 sync_id ↔ 能力集 token 映射（0094 归置表 20 项全量——v1 仅 5 项致 31 项能力漏网）
SYNC_ID_CAP_MAP: dict[str, str] = {
    # hist_quote
    "astock_daily": "hist_quote", "etf_daily": "hist_quote", "cb_daily": "hist_quote",
    "index_daily": "hist_quote", "astock_minute": "hist_quote", "astock_minute_5min": "hist_quote",
    # ref_data
    "astock_basic": "ref_data", "astock_list": "ref_data", "etf_list": "ref_data",
    "cb_basic": "ref_data", "stk_limit_sync": "ref_data", "moneyflow_sync": "ref_data",
    "margin_detail_sync": "ref_data", "top_list_sync": "ref_data", "block_trade_sync": "ref_data",
    "cyq_perf_sync": "ref_data", "forecast_sync": "ref_data", "namechange_sync": "ref_data",
    "concept_sync": "ref_data", "trade_cal": "ref_data",
}
NON_DATA_PROVIDERS = {"tencent": {"rt_quote"}, "xtp": {"trading", "rt_quote"},
                      "binance_perp": {"trading", "rt_quote"}, "okx_perp": {"trading", "rt_quote"},
                      "emt_emq": {"trading", "rt_quote"}}   # 无 adapter 的通道能力（D25 token 化：tencent 分钟已退役 0096，快照/分时均归 rt_quote）
# emt_emq=东方财富 EMT 极速柜台(交易)+EMQ 极速行情(行情)——批 63 插件化首个新 provider，能力直标
# 键=Broker._REGISTRY/同步路由的真实 provider 串（binance_perp/okx_perp 非 binance/okx——
# 55a 修键：与 26 号收尾批 C 对齐，词表键错位会让 ⊆ 校验误拒合法配置）

# provider → 市场（固定归属；55a 写侧校验 market 与 provider 一致，防跨市场错行）
PROVIDER_MARKET: dict[str, str] = {
    "tushare": "astock", "joinquant": "astock", "ricequant": "astock", "tencent": "astock",
    "xtp": "astock", "binance_perp": "crypto", "okx_perp": "crypto",
    "emt_emq": "astock",
}

# 权限五键 → (market, category, exchange) 无损映射(market_op 三分混一的立法化解)
MARKET_OP_DECOMP: dict[str, tuple] = {
    "astock": ("astock", "stock", None), "etf": ("astock", "etf", None),
    "convertible": ("astock", "convertible", None),
    "binance_perp": ("crypto", "perp", "BINANCE"), "okx_perp": ("crypto", "perp", "OKX"),
}


# 能力查询/校验在 data_platform/capabilities.py(层 0 禁 import 上层——test_layering 铁律;
# 词表与映射是纯数据留此处,消费逻辑在数据层)
