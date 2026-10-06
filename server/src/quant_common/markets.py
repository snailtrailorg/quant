"""批55-0:市场/交易所/品类/能力 维度注册表(全系统单源——批33b perm_registry 同模式)。

概念立法(2026-09-19 用户终裁方案一 v2; D25 v2 2026-09-25 能力集重构):
- 行的边界=账号(连接身份);列的语义=能力集;配置页签=能力筛选(D25:页签合并为单列表)
- 能力真源=DataKind(contract.py 27 词);能力集=派生归类视图(KIND_CAP_CLASS 单源映射,
  非独立建模);配置列恒为「用户启用子集」(写侧校验 ⊆)
- 词表三层(D25):代码层 sync_id(品类×粒度复合串) → DataKind(细粒度能力) → 能力集 token(5 类)
- token 立法:trading 保词(**能力集 token 名不动**);批 83a 拆表后 `'trading' = ANY(capabilities)`
  域谓词在表层面退役(域=表,见 DOMAIN_* 段)——只剩能力集校验用途
- daily/minute/snapshot/quote 四旧词退役(SQL 消费唯 routing._CAP_KIND_ALIASES 一处随派生表重写)
"""
from __future__ import annotations

# MarketSpec（批 66c，D26 §3.2 纯数据声明——骨架层；market_hours/market_session 表=运营真源不变）：
#   trading_day_anchor：交易日翻转锚（natural=自然日〔A 股 C4 跨日清累计〕/utc0/night_open——期货夜盘预留）
#   sessions_skeleton：时段骨架（列表=日盘分段 [(open,close)...]；"24x7"=全天候）——session.py 无配置
#     fallback 与 worker ts 缺口检测的段判定单源；精确到分钟/节假日归 market_session 表
#   channels：数据通道旋钮（per-market stream_maxlen 上界——hub STREAM_MAXLEN 缺省同源）
#   flush_policy：分钟桶收口策略（three_window=A股三窗 finalize〔11:29/14:59/15:01〕；
#     stale_5min=加密每 5 分钟 flush_stale）——hub main._flush 消费点注释挂靠此声明（值不动）
MARKETS: dict[str, dict] = {
    "astock": {"name_zh": "A股", "timezone": "+08:00",
               "trading_day_anchor": "natural",
               "sessions_skeleton": [("09:31", "11:30"), ("13:01", "15:00")],
               "channels": {"bar": {"stream_maxlen": 5000}},
               "flush_policy": "three_window"},
    "crypto": {"name_zh": "加密", "timezone": "UTC",
               "trading_day_anchor": "utc0",
               "sessions_skeleton": "24x7",
               "channels": {"bar": {"stream_maxlen": 5000}},
               "flush_policy": "stale_5min"},
}


def market_of_symbol(symbol: str) -> str:
    """symbol 后缀（.SHSE/.BINANCE 等）→ 市场域（EXCHANGES 单源派生；未知后缀=astock 缺省）。"""
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    return EXCHANGES.get(suffix, {}).get("market", "astock")

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

# 代码层 sync_id ↔ 能力集 token 映射（键集须 ⊇ sync_config.id ∪ sync_kind_config.sync_id；
# 批 99 起钉「完备性」而非字面量键数——加同步项忘了登记即红。v1 仅 5 项致 31 项能力漏网是前科）
SYNC_ID_CAP_MAP: dict[str, str] = {
    # hist_quote
    "astock_daily": "hist_quote", "etf_daily": "hist_quote", "cb_daily": "hist_quote",
    "index_daily": "hist_quote", "astock_minute": "hist_quote", "astock_minute_5min": "hist_quote",
    # 批 101：加密永续日线（币安批量历史，历史行情类）
    "binance_perp_daily": "hist_quote",
    # 批 103b：聚宽 A 股历史切片——**独立 sync_id**，不占 `astock_daily` 的切换位
    # （试用窗口无最近 3 个月，不能当 astock_daily 的常规替代源；威廉姆 2026-10-06 裁定）
    "astock_daily_jq": "hist_quote",
    # ref_data
    "astock_basic": "ref_data", "astock_list": "ref_data", "etf_list": "ref_data",
    "cb_basic": "ref_data", "stk_limit_sync": "ref_data", "moneyflow_sync": "ref_data",
    "margin_detail_sync": "ref_data", "top_list_sync": "ref_data", "block_trade_sync": "ref_data",
    "cyq_perf_sync": "ref_data", "forecast_sync": "ref_data", "namechange_sync": "ref_data",
    "concept_sync": "ref_data", "trade_cal": "ref_data",
    # ref_data（0106：pool_data per-symbol 族——financial_stmt/featured_daily/holder_structure 派生类均 ref_data）
    "income": "ref_data", "balancesheet": "ref_data", "cashflow": "ref_data",
    "fina_indicator": "ref_data", "cyq_chips": "ref_data", "top10_holders": "ref_data",
    "dividend": "ref_data", "pledge_stat": "ref_data", "share_float": "ref_data",
    "stk_holdernumber": "ref_data",
    # 批 99：补 4 缺项——有 sync_config 行却在归置键/能力声明双双缺席（批 83b 收编 0118/0119
    # 时漏更新；与「清单有·能力无」同族）。补后本表 ⊇ sync_config.id ∪ sync_kind_config.sync_id，
    # 由 tests/test_markets_registry 的完备性钉守（不再是字面量 30 键）。
    "convertible_terms": "ref_data", "static_symbols": "ref_data",
    "pool_data": "ref_data", "pool_data_full_calibrate": "ref_data",
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
    # 批 101：加密**数据源** provider（`binance` = 批量历史 adapter，0 密钥）——注意与交易
    # 通道 `binance_perp`（NON_DATA_PROVIDERS，trading/rt_quote）**是两把键**：一个是拉数面、
    # 一个是下单面。混用会让 `_get_supply_adapter` 找不到 adapter 而静默回落 tushare 拉错源。
    "binance": "crypto",
}

# 批 83a 拆表立法（2026-09-29 用户裁定·抽象判据）：行的边界=账号，**域=表**。
# 两个配置域 = 两张表 = 两个端点族（三者同源）。域词=表名（避免与能力 token 'trading' 撞词）。
# 合表时代（55a external_interface）用 `'trading' = ANY(capabilities)` 域谓词临时区分两类账号；
# 拆表后该谓词**在表层面退役**（表本身即域，消费侧零谓词）。
DOMAIN_DATA = "data_source"          # 数据源域（拉取侧：token/限速/熔断/用量/pacer）
DOMAIN_TRADING = "trading_account"   # 交易账号域（下单/行情侧：连接参数/资金账号/交易所覆盖）

# 域能力集（收窄立法）：写入某域行的 capabilities 必须 ⊆ 该域集。
# 合表时代可建出「交易行挂 hist_quote」这类揉合行；拆表后写侧不可能建出（前端每页签也收窄）。
# 两集互斥且并集=CAPABILITIES（test_markets_registry 断言守门）。
DOMAIN_CAPS: dict[str, tuple] = {
    DOMAIN_DATA: ("hist_quote", "ref_data", "inst_event"),
    DOMAIN_TRADING: ("rt_quote", "trading"),
}

# 权限五键 → (market, category, exchange) 无损映射(market_op 三分混一的立法化解)
MARKET_OP_DECOMP: dict[str, tuple] = {
    "astock": ("astock", "stock", None), "etf": ("astock", "etf", None),
    "convertible": ("astock", "convertible", None),
    "binance_perp": ("crypto", "perp", "BINANCE"), "okx_perp": ("crypto", "perp", "OKX"),
}


# 能力查询/校验在 data_platform/capabilities.py(层 0 禁 import 上层——test_layering 铁律;
# 词表与映射是纯数据留此处,消费逻辑在数据层)
