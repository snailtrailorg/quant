"""历史 K 线数据源 adapter 基类 + 注册表（24 号多数据源架构）。

职责分层（组合不继承，复审 B-P1-5）：
- DataSource（data_source.py）：连接/token/限速/熔断/用量
- BaseDataAdapter（本文件）：K 线拉取 + 字段映射到统一 11 字段

加聚宽/米筐 = 实现 BaseDataAdapter 子类 + 注册 _ADAPTERS + DB 配 sync_config.provider。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from src.quant_common.contract import ASTOCK_ALL, CapabilityDecl


class UnsupportedFeature(Exception):
    """该数据源不支持某能力（如按日全市场批量拉取）。真接批实现 per-symbol 降级，本批只抛出。"""


class BaseDataAdapter(ABC):
    """历史 K 线数据源 adapter（对齐 vnpy BaseDatafeed）。

    per-symbol 拉取是核心（所有源都实现）；按日批量是能力方法（Tushare 专属）。
    to_bar_rows 是「统一 11 字段」唯一出口——所有源拉取返回源原生 DataFrame，
    to_bar_rows 转统一 (symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)。
    """
    provider: str = ""
    # 批 108·步 2：本源对应的**交易所后缀**（vt_symbol 的 `.XXX` 段）。**仅「单交易所源」
    # （crypto）有意义**——多交易所源（tushare/joinquant 一条 sync 覆盖沪深北）留空，
    # 其交易所由 ts_code 派生（`schema.to_vt_symbol`）。
    venue: str = ""
    # 能力矩阵（24 号）：该平台能提供的同步项 sync_id 集合。供给矩阵只列这些
    # （由 adapter 类属性派生，非 DB JSON——盲审 A-P2/B-P2 双真相源会漂移）
    capabilities: set[str] = set()

    # ——— 批 102a：出口配置（代理 / 端点覆盖）———
    # 只有**自己发 HTTP** 的 adapter 才置 True（出口可注入代理）；SDK 型（tushare/jqdatasdk
    # 的官方客户端不暴露 per-call 代理）保持 False ⇒ 为其配代理会被 API 写入侧**响亮拒绝**
    # （400，见 `web_api/routes/proxies.py`），**不静默绕过**。
    supports_exit_config: bool = False

    def configure_exit(self, proxy: str | None = None, endpoint: str | None = None) -> None:
        """注入出口配置。**engine 在构造 adapter 时解析一次并调用**（`_apply_exit_config`）。

        本方法**不触库**——解析在 engine 侧，adapter 只持有结果。这样两点成立：
        ① 语义＝「每任务启动解析一次」（adapter 由 engine 每任务新建）；
        ② 直接手搓 adapter 的单测（`_pull`/`list_symbols`）零 DB 耦合。
        """
        if proxy and not self.supports_exit_config:
            raise UnsupportedFeature(
                f"{self.provider} adapter 未接线代理出口（supports_exit_config=False）——"
                f"为其配代理不会生效，拒绝静默绕过")
        self._proxy, self._endpoint = proxy, endpoint          # type: ignore[attr-defined]

    def exit_config(self) -> tuple[str | None, str | None]:
        """→ `(proxy_dsn, endpoint_override)`；**未注入 ⇒ `(None, None)`＝直连**。不触库。"""
        return (getattr(self, "_proxy", None),                        # type: ignore[attr-defined]
                getattr(self, "_endpoint", None))                     # type: ignore[attr-defined]

    @abstractmethod
    def pull_daily(self, symbol: str, start: str, end: str, adj=None, kind: str = "astock") -> pd.DataFrame:
        """per-symbol 日线。symbol=ts_code（如 600000.SH）；kind=astock/etf/cb（源接口路由）。"""

    @abstractmethod
    def pull_minute(self, symbol: str, freq: str, start: str, end: str) -> pd.DataFrame:
        """per-symbol 分钟线。"""

    @abstractmethod
    def to_bar_rows(self, df: pd.DataFrame, freq: str, adj_map: dict | None = None) -> list[tuple]:
        """字段映射 → 统一 11 字段。

        - source = self.provider
        - adj_map 非 None = 批量路径（pro.daily 无 adj_factor 列，因子来自外部 adj_map）；
          adj_map is None = per-symbol 路径（因子来自 df["adj_factor"]）
        - freq 含 "min" 读 trade_time 列，否则 trade_date 列
        """

    def pull_daily_batch(self, trade_date: str, kind: str) -> pd.DataFrame:
        """按日全市场批量（Tushare pro.daily/fund_daily 专属）。默认 UnsupportedFeature。"""
        raise UnsupportedFeature(f"{self.provider} 不支持按日批量")

    def fetch(self, req, acct=None):
        """批 58·M3：拉取统一契约（28 §5.2——同步引擎循环与消费面 DataBus 共用，区别只在 req）。

        req=FetchRequest（kind/sub_kind/symbols/range_/freq/as_of/mode/...）；acct=AccountRef。
        分派键=(kind, sub_kind)（DataKind 第一公民——bar_daily 的 stock/etf/convertible 靠 sub_kind
        判别，sync_id 不复活）；拉取粒度（逐日批/per-symbol/区间）是 adapter 内部批量优化策略。
        返回 ContractFrame（to_contract 产物——58 同批建，本抽象先占位）。
        """
        raise UnsupportedFeature(f"{self.provider} 未实现 fetch({getattr(req, 'kind', '?')})")

    def fetch_supply(self, kind: str, sub_kind: str | None = None, **params) -> pd.DataFrame:
        """批 100：供给面拉取端口（按 `(kind, sub_kind)` 分派，返回**源原生 DataFrame**）。

        与 `fetch`（消费/供给统一契约，返回 `ContractFrame`）分开：本端口给「全市场日均 /
        快照清单 / 逐年」这类**写入面**同步项用——它们的落库列形状真源是 `sync_kind_config`
        的 `pk_cols/float_cols/text_cols` + 目标表，引擎沿用原 upsert（**零行为漂移**）。
        全契约化（改返回 ContractFrame + adapter 侧列声明）留后续小批，避免列形状双真源。

        分派键＝`(kind, sub_kind)`（DataKind 第一公民，`sync_id` 不复活）；`params` 透传源侧
        窗口参数（`trade_date`/`ann_date`/`year`/...）。默认不支持即抛 `UnsupportedFeature`。
        """
        raise UnsupportedFeature(
            f"{self.provider} 未实现 fetch_supply(kind={kind}, sub_kind={sub_kind})")

    def pull_adj_factor(self, symbol=None, trade_date=None, start=None, end=None) -> pd.DataFrame:
        """复权因子（Tushare adj_factor 专属）。默认空（因子缺省 NULL）。

        symbol + start/end = 单标的区间因子；trade_date = 全市场单日因子。
        """
        return pd.DataFrame()

    # —— 源可达区间（批 108·步 1；设计 §5.1 第四边界 / §八.1 硬契约）——

    def available_range(self, kind: str) -> tuple[str | None, str | None]:
        """源侧可达区间 `(earliest, latest)`，ISO `'YYYY-MM-DD'`——**窗口第四边界的唯一真源**。

        - `earliest` ＝ 源在 `kind` 上**最早可得**的数据日期（`None` ＝ 源不构成下界约束）。
        - `latest`   ＝ 源在 `kind` 上**最晚可得**的数据日期；`None` ＝「**随今日滚动**」
          （由调用方按 `publish_lag` 回推），**不是**「不知道」。

        **为什么要有它（设计 §5.1）**：模型若只有上界，adapter 会在内部**私自收窄起点**
        （先例＝`binance_adapter._pull` 的省流阀），于是「模型窗口 ≠ 真实窗口」，
        对账层便不可信——「拉回了空」与「源本来就没有」**同形**。

        **硬契约（设计 §八.1 / 双盲审① P0-2）**：所有**可拉** adapter（`capabilities` 非空）
        **必须覆写**本方法。未覆写的子类落进本默认实现 ⇒ **`NotImplementedError`（fail-loud）**；
        **禁止**返回 `today` / `(None, None)` 冒充——漏实现会让新源默认「今天起」，
        历史**永不回补**且无告警（source_upper 变软真源）。

        粒度＝**kind 级**（不是 per-symbol）：源可达性逐标的不同由 **inception**
        （SM `list_date`）承担，两者取 `max`（设计 §5.1「粒度陷阱与绑定点」）。

        `kind` ＝ DataKind 词（`bar_daily` / `bar_minute` / `index_daily` / …）。
        **取值须实测、禁臆造**（先例＝迁移 `0137`「只填已实证下界，不臆造」）。
        """
        raise NotImplementedError(
            f"{self.provider} adapter 未实现 available_range(kind={kind!r})——"
            f"源可达下界（窗口第四边界）无真源；禁止以 today/None 冒充（设计 §八.1 硬契约）")

    def publish_lag(self, kind: str) -> int:
        """源发布滞后（**自然日**）：数据「已发生」→「源可得」的时延。默认 `0`＝盘后当日可得。

        窗口上界 ＝ `available_range(kind).latest − publish_lag`（`latest is None` 时以今日推）。
        收编原两处启发式的语义（设计 §六）：`_CRYPTO_TAIL_GRACE`（批量站 T+1 发布，自然日）
        与 `_TIER1_LAG_TRADING_DAYS`（T+1 表，**交易日**）——后者单位不同，收编时由调用点
        按日历换算，**本方法统一以自然日计**。
        """
        return 0

    def symbol_inception(self, symbol: str) -> str | None:
        """该标的的**产生时间**（上币/上市日），ISO `'YYYY-MM-DD'`；**不可得 ⇒ `None`＝显式「未知」**。

        窗口起点第一地板 `inception` 的**源侧取值口**（设计 §八.2 / §5.4）。三条约法：

        1. **禁以源可达性冒充**（裁定 F / §九.3）：`available_range().earliest` 是**源属性**、
           `first_available_month()` 是**批量路径优化边界**——二者都 ≠ 上币日（OKX 差 ≈2 月、
           Binance 差 ≈4 月，两手实测）。拿它们当 inception ⇒「**不认为缺失**」（静默）。
        2. **不可得就返回 `None`**：调用方须按 §5.4 将该标的/族标 `uncertain`
           （**抑制一切缺口主张**），**不得**退化成某个「看起来合理」的日期。
        3. 默认 `None` ＝本源不提供标的级生命周期（如 Binance：`fapi` 被墙 ⇒ `onboardDate` 不可得）。
        """
        return None

    # —— 归一化（带默认实现，Tushare 直通，聚宽/米筐覆写，24 号 §2.1）——
    def to_source_symbol(self, symbol: str) -> str:
        """内部 ts_code（600000.SH）→ 源格式（聚宽 600000.XSHG）。默认直通。"""
        return symbol

    def from_source_symbol(self, source_symbol: str) -> str:
        """源格式 → 内部 ts_code（to_bar_rows 归一到 vt_symbol 的输入）。默认直通。"""
        return source_symbol

    def to_source_freq(self, freq: str) -> str:
        """内部 freq（'1min'）→ 源格式（聚宽 '1m'）。默认直通。"""
        return freq

    def to_source_adj(self, adj: str | None) -> str | None:
        """语义复权 → 源格式。默认直通。

        注意（盲审 A-P2/B-P2）：当前内部约定即 Tushare 原语 'qfq'/'hfq'/None（tushare_adapter
        直用），Tushare 直通。接聚宽/米筐时统一为语义化 'pre'/'post'，各源 to_source_adj
        做映射（聚宽 pre→fq='pre'、Tushare pre→qfq）——真接批一并统一，本批只预留接口。
        """
        return adj


# ——— 注册表 ———
_ADAPTERS: dict[str, type[BaseDataAdapter]] = {}


def register_adapter(cls: type[BaseDataAdapter]) -> type[BaseDataAdapter]:
    """注册 adapter（装饰器）。

    批 56a·M0（29 号 §三）：capability_decls 即时校验钩子（import 即执法）——
    类声明了 capability_decls 属性则逐项过组合表校验（validate_capability_decls），
    非法即 ValueError 拒绝注册。现状 adapter 无该声明=空转合法（M3 收编时起真声明）。
    """
    decls = getattr(cls, "capability_decls", None)
    if decls is not None:
        from src.quant_common.contract import validate_capability_decls
        errors = validate_capability_decls(decls)
        if errors:
            raise ValueError(f"adapter {cls.provider} capability_decls 非法: {errors}")
    _ADAPTERS[cls.provider] = cls
    return cls


def get_adapter(provider: str) -> BaseDataAdapter:
    """从注册表实例化 adapter。provider 未注册抛 ValueError。"""
    cls = _ADAPTERS.get(provider)
    if not cls:
        raise ValueError(f"未注册的数据源 adapter: {provider}")
    return cls()


def list_adapters() -> dict[str, type[BaseDataAdapter]]:
    """注册表只读快照（provider → 类）。批 102a 用于派生「可配代理的消费方」集合。"""
    return dict(_ADAPTERS)


@register_adapter
class TushareAdapter(BaseDataAdapter):
    """Tushare 历史 K 线 adapter（mapper 三合一 + 统一走 DataSource，24 号）。

    盲审 A-P1/B-P1 修复：缓存 self._ds，pull_daily/pull_minute/pull_adj_factor/pull_daily_batch
    全走 self.get_client()（DataSource DB 解密 token + 限速/熔断/用量），废弃模块级 get_pro
    （.env token）——消除双 pro 实例/token 源不一致。
    """

    provider = "tushare"
    # D25：⊆ 校验宇宙=本声明——0094 归置表 20 个 sync_id 全量（v1 仅 5 项致 15 项能力用户勾不了）
    # 批 99：补 4 缺项（convertible_terms/static_symbols/pool_data/pool_data_full_calibrate）——
    # 它们有 sync_config 行却在能力声明与 SYNC_ID_CAP_MAP 双双缺席（「清单有·声明无」，
    # 后果＝前端 provider 下拉对这 4 行恒空、_validate_provider 拒改源）。
    capabilities = {
        "astock_daily", "etf_daily", "cb_daily", "index_daily",
        "astock_minute", "astock_minute_5min",
        "astock_basic", "astock_list", "etf_list", "cb_basic",
        "stk_limit_sync", "moneyflow_sync", "margin_detail_sync", "top_list_sync",
        "block_trade_sync", "cyq_perf_sync", "forecast_sync", "namechange_sync",
        "concept_sync", "trade_cal",
        "convertible_terms", "static_symbols", "pool_data", "pool_data_full_calibrate",
        "st_list_sync",   # 批 117：ST 官方名单（featured_daily 族）
    }

    # 批 99：契约层能力声明（contract.CapabilityDecl）——**真声明**，激活 `register_adapter`
    # 里装好的 `validate_capability_decls` 钩子（此前零 adapter 声明 ⇒ 钩子空转）。
    # 粒度＝kind（不是 sync_id）：同一 (kind, temporality) 可被多源实现，供应商差异落在
    # to_source_* 映射方法上（「同一接口多实现」而非「一数据一接口」）。
    # sub_kinds 仅聚合域声明（contract.validate_capability_decls 硬约束：聚合域必须非空、
    # 非聚合域必须为空——故 bar_daily/bar_minute 不带 sub_kinds，其 stock/etf/convertible
    # 判别在 sync_kind_config 行上）。
    capability_decls = [
        # 行情（hist_quote）
        CapabilityDecl("bar_daily", "historical", ASTOCK_ALL),
        CapabilityDecl("bar_minute", "historical", ASTOCK_ALL),
        CapabilityDecl("index_daily", "historical", ASTOCK_ALL),
        CapabilityDecl("adj_factor", "historical", ASTOCK_ALL),
        # 参考数据（ref_data）
        CapabilityDecl("fundamental_daily", "historical", ASTOCK_ALL),
        CapabilityDecl("stk_limit", "historical", ASTOCK_ALL),
        CapabilityDecl("trade_cal", "historical", ASTOCK_ALL),
        CapabilityDecl("static_list", "historical", ASTOCK_ALL,
                       sub_kinds=frozenset({"stock", "etf", "convertible", "namechange",
                                            "symbols", "terms"})),   # 批 107：+symbols/+terms（字面量收编）
        CapabilityDecl("industry_class", "historical", ASTOCK_ALL,
                       sub_kinds=frozenset({"concept"})),
        CapabilityDecl("featured_daily", "historical", ASTOCK_ALL,
                       sub_kinds=frozenset({"moneyflow", "margin_detail", "top_list",
                                            "block_trade", "cyq_perf", "cyq_chips"})),
        CapabilityDecl("financial_stmt", "historical", ASTOCK_ALL,
                       sub_kinds=frozenset({"forecast", "income", "balancesheet",
                                            "cashflow", "fina_indicator"})),
        CapabilityDecl("holder_structure", "historical", ASTOCK_ALL,
                       sub_kinds=frozenset({"top10_holders", "dividend", "pledge_stat",
                                            "share_float", "stk_holdernumber"})),
    ]

    def __init__(self):
        from src.data_platform.data_source import TushareDataSource, get_data_source
        self._ds = get_data_source("tushare") or TushareDataSource()
        self._pro = None

    def get_client(self):
        """Tushare pro 客户端（进程级缓存，避免每次调用重建 pro/读 DB 解密——盲审 A-P2/B-P2）。"""
        if self._pro is None:
            self._pro = self._ds.get_client()
        return self._pro

    def pull_daily(self, symbol: str, start: str, end: str, adj=None, kind: str = "astock") -> pd.DataFrame:
        """per-symbol 日线。kind 路由到 daily/fund_daily/cb_daily（盲审 A-P1-2：pro_bar 默认
        asset='E' 股票，对 ETF/转债返回空——原 _get_pro_api 按 kind 路由三条 API）。"""
        from datetime import date as _date
        pro = self.get_client()
        end = end or _date.today().strftime("%Y%m%d")
        if kind == "etf":
            df = pro.fund_daily(ts_code=symbol, start_date=start, end_date=end)
        elif kind == "cb":
            df = pro.cb_daily(ts_code=symbol, start_date=start, end_date=end)
        else:
            try:
                df = pro.pro_bar(ts_code=symbol, freq="D", start_date=start, end_date=end, adj=adj)
            except Exception:
                df = pro.daily(ts_code=symbol, start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame()
        if "adj_factor" not in df.columns:
            df["adj_factor"] = None
        return df

    def pull_minute(self, symbol: str, freq: str, start: str, end: str) -> pd.DataFrame:
        from datetime import date as _date
        pro = self.get_client()
        end = end or (_date.today().strftime("%Y%m%d") + " 15:00:00")
        df = pro.stk_mins(ts_code=symbol, freq=freq, start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame()
        df["adj_factor"] = None
        df["trade_date"] = df["trade_time"].str[:10].str.replace("-", "")
        return df

    def to_bar_rows(self, df: pd.DataFrame, freq: str, adj_map: dict | None = None) -> list[tuple]:
        """三合一 mapper：原 _daily_to_rows（批量，adj_map）+ to_save_rows（日）+ to_save_rows_min（分钟）。"""
        from src.data_platform.adapters.tushare_adapter import _safe_float
        from src.data_platform.schema import to_vt_symbol
        from src.data_platform.tz import as_utc
        rows = []
        is_daily = "min" not in freq
        for row in df.to_dict("records"):
            ts_code = row.get("ts_code", "")
            vt_sym = to_vt_symbol(ts_code)
            # ts：分钟读 trade_time，日线读 trade_date；+08:00 aware（26 号收尾批 C）
            if "min" in freq:
                ts = as_utc(pd.Timestamp(row["trade_time"]).to_pydatetime())
            else:
                ts = as_utc(pd.Timestamp(row["trade_date"]).to_pydatetime())
            # adj：批量路径（adj_map 非 None）用 adj_map；per-symbol（None）用 df["adj_factor"]
            if adj_map is not None:
                adj_raw = adj_map.get(ts_code)
                adj_val = float(adj_raw) if adj_raw is not None and pd.notna(adj_raw) else None
            else:
                adj_val = _safe_float(row.get("adj_factor")) if row.get("adj_factor") and pd.notna(row.get("adj_factor")) else None
            # 单位换算（专家审核 P0，2026-09-07 定性：Tushare 日线 vol=手/amount=千元，分钟线
            # stk_mins vol=股/amount=元——日线 ×100/×1000 到统一契约股/元；分钟线不换算）。
            # 实证：600000.SH 09-04 daily vol=757659.82 手（bar_1D 曾同值）vs bar_hub sum=75522582 股
            vol = _safe_float(row.get("vol", 0))
            amt = _safe_float(row.get("amount", 0))
            if is_daily:
                vol *= 100
                amt *= 1000
            rows.append((
                vt_sym, freq, ts,
                _safe_float(row["open"]), _safe_float(row["high"]), _safe_float(row["low"]),
                _safe_float(row["close"]),
                vol, amt,
                adj_val, self.provider,
            ))
        return rows

    def pull_daily_batch(self, trade_date: str, kind: str) -> pd.DataFrame:
        pro = self.get_client()
        if kind == "astock":
            return pro.daily(trade_date=trade_date)
        if kind == "etf":
            return pro.fund_daily(trade_date=trade_date)
        raise UnsupportedFeature(f"tushare 不支持 kind={kind} 按日批量")

    # 批 108·步 1：源可达下界（kind 级，见 available_range 覆写处的语义注）。
    SOURCE_EARLIEST = "2010-01-01"

    def available_range(self, kind: str) -> tuple[str | None, str | None]:
        """见基类契约。**kind 分级（批 108 复核修正——设计稿 §八.1 单一取值会造成回归）**：

        - `index_daily` → **`(None, None)`**：基准指数**无 inception**（设计 §九.7），其窗口
          下界只能靠 `retention`（现＝`20050408`）；而 tushare 指数数据远早于该值 ⇒ 若在此返回
          `2010-01-01`，则 `max(retention=2005-04-08, source=2010-01-01)` ＝ **2010**
          ⇒ **静默丢掉 2005-2009 历史**（对已上线族的直接回归）。故本源下界对该族显式「无界」。
        - `fundamental_daily` → **`1990-12-19`**（批 108·步 3 补，**实测值**）：上游
          `daily_basic` 的最早行（迁移 `0131` 已记录，`astock_basic` 的 `retention` 也是它）。
          为何必须补：`astock_basic` 的 `retention='1990-12-19'` 若被 `2010-01-01` 抬起 ⇒
          `max` ＝ 2010 ⇒ **裁掉 1990-2009 这段合法区间**（「省流阀不得裁掉合法区间」族）。
          其余 kind 的**真实**源下界未实测 ⇒ 仍在下一行用保守值（挂账 G-1 未闭）。
        - 其余 kind → `SOURCE_EARLIEST`：这些族的**个股 inception** 已被 `_get_list_date` 的
          「早于 2010 抬到 2010」（`engine.py`）夹住 ⇒ `max` 结果不变、**不回归**。

        ⚠️ **语义注（已知混装，同为「一列多义」族——旧 `start_floor` 已由批 108·步 3 拆正名）**：
        tushare 的真实可达性受**账号
        积分/权限**约束，而 `SOURCE_EARLIEST` 与 `engine._TUSHARE_MIN_DATE`（env
        `SYNC_START_DATE`）**同值同源**——即本返回值同时承载「源可达」与「我们配置的起点」
        两层语义。取**保守偏晚**值：用它当窗口下界只会「少拉本就不在系统范围内的更早历史」，
        **不会**漏拉范围内的数据、也不会产生假缺口。（拆义挂账见任务文件 §挂账。）
        """
        if kind == "index_daily":
            return (None, None)
        if kind == "fundamental_daily":
            return ("1990-12-19", None)      # 上游 daily_basic 实测最早行（迁移 0131 记录）
        return (self.SOURCE_EARLIEST, None)

    def pull_adj_factor(self, symbol=None, trade_date=None, start=None, end=None) -> pd.DataFrame:
        """复权因子。保留 None（接口不可用）与空 df（无数据）区分——backfill_adj_factor 靠 None 判 degraded。"""
        from src.data_platform.adapters.tushare_adapter import _adj_degraded_alert
        pro = self.get_client()
        try:
            if symbol:
                df = pro.adj_factor(ts_code=symbol, start_date=start or "", end_date=end or "")
            else:
                df = pro.adj_factor(trade_date=trade_date)
            if df is None or df.empty:
                return pd.DataFrame()
            return df[["ts_code", "trade_date", "adj_factor"]]
        except Exception as e:
            _adj_degraded_alert(e)
            return None

    def fetch(self, req, acct=None):
        """批 58·M3 拉取统一契约（28 §5.2——同步/消费共用；58a bar 族 + 批 83b 池内族）。

        分派键=(kind, sub_kind)（DataKind 第一公民，sync_id 不复活）；bar_daily+stock/etf 再按
        req.mode 分叉（批 75·H7）：
        - supply（同步引擎）= 按日全市场批拉（pull_daily_batch——引擎循环逐日调，req.range_ 单日）
          附当日全市场复权因子 adj_map（对齐已退役 _daily_to_save_fn〔批 72〕——astock 因子非 NULL 生死线）
        - consume（DataBus fetch-on-miss）= per-symbol 单标的区间（pull_daily，不复权+逐行 adj_factor）
        - bar_daily+convertible = 区间全量（pull_cb_daily）
        - index_daily = per-symbol 区间（pull_index_daily）
        - bar_minute = per-symbol 区间（split_minute_range 分段 + 09:00/15:00 约定，对齐 _pull_minute）
        - financial_stmt / featured_daily / holder_structure = per-symbol 池内深度数据
          （sub_kind=表名；形状由 POOL_TABLE_SPECS 声明，见下）
        拉取粒度差异是 adapter 内部批量优化策略，不改签名。返回 to_contract
        （bar 族 11 字段+UTC 校验；池内族按声明列序校验——两条形状律见 contract.to_contract）。
        其余非 bar 族（fundamental_daily 等）列形状未立法，仍抛 UnsupportedFeature。
        """
        from src.quant_common.contract import to_contract
        from src.data_platform.adapters.tushare_adapter import (
            POOL_FETCH_KINDS, POOL_TABLE_SPECS, pull_pool_table, to_pool_rows)
        kind = getattr(req, "kind", "")
        sub = getattr(req, "sub_kind", None)
        freq = getattr(req, "freq", None) or "1D"
        rng = getattr(req, "range_", None)
        start = rng[0].strftime("%Y%m%d") if rng and rng[0] else None
        end = rng[1].strftime("%Y%m%d") if rng and rng[1] else None

        if kind == "bar_daily":
            if sub == "convertible":
                from src.data_platform.adapters.tushare_adapter import pull_cb_daily
                df = pull_cb_daily(start, end)
                # 批 72 质量校验迁移面（v2 #2 双同：空 df 闸——节假日/非交易区间不喷噪声）
                if df is not None and not df.empty:
                    _log_bar_quality_local(df, "cb_daily")
                rows = self.to_bar_rows(df, freq)
            elif getattr(req, "mode", "consume") == "consume":
                # 批 75·H7：消费面口径——per-symbol 单标的区间拉取（fetch-on-miss 兜长尾，28 §7.2）。
                # 不复权（adj=None=原始价）+ 逐行 adj_factor（to_bar_rows adj_map=None 分支读
                # df["adj_factor"]——pro_bar 原生带列，回落 pro.daily 缺列补 None）——对齐 bar_1D 契约。
                # 限速档对齐 DEFAULT_RATE_LIMITS 键：stock=daily / etf=fund_daily（逐标的各一次
                # 上下文=一次进账/熔断计数，对齐供给域逐 API 记账口径）。
                from src.data_platform.rate_limit import rate_limit_context
                api = "fund_daily" if sub == "etf" else "daily"
                kind_param = "etf" if sub == "etf" else "astock"
                dfs = []
                for sym in (req.symbols or ()):
                    with rate_limit_context(self._ds, api):
                        dfs.append(self.pull_daily(sym, start, end, adj=None, kind=kind_param))
                df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
                if df is not None and not df.empty:
                    _log_bar_quality_local(df, "bar_daily" if sub == "stock" else "etf_daily")
                rows = self.to_bar_rows(df, freq)
            else:   # stock/etf：按日全市场批拉（sub_kind=stock→astock）+ 当日复权因子 adj_map
                df = self.pull_daily_batch(start, "astock" if sub == "stock" else sub)
                adj_map = {}
                # 空 df（节假日 freq=B 拉到空）不拉因子，对齐已退役 _daily_to_save_fn〔批 72〕只在 df 非空时拉因子
                if start and df is not None and not df.empty:
                    # 批 72 质量校验迁移面（落点=非空块内 adj_map 拉取前——v2 #2 双同锚点）
                    _log_bar_quality_local(df, "bar_daily" if sub == "stock" else "etf_daily")
                    # adj_factor 独立限速（adj_factor 档，对齐已退役旧路径 _adj_map_for_df〔批 72〕批 58 补的限速）——
                    # 嵌套在引擎 daily 档 with 块内，二者独立 limiter 各自 sleep，时序对齐旧路径
                    from src.data_platform.rate_limit import rate_limit_context
                    with rate_limit_context(self._ds, "adj_factor"):
                        fdf = self.pull_adj_factor(trade_date=start)
                    if fdf is not None and not fdf.empty:
                        adj_map = dict(zip(fdf["ts_code"], fdf["adj_factor"]))
                rows = self.to_bar_rows(df, freq, adj_map)
        elif kind == "index_daily":
            from src.data_platform.adapters.tushare_adapter import pull_index_daily
            sym = req.symbols[0] if req.symbols else ""
            df = pull_index_daily(sym, start, end)
            rows = self.to_bar_rows(df, freq)
        elif kind == "bar_minute":
            from src.data_platform.adapters.tushare_adapter import split_minute_range
            sym = req.symbols[0] if req.symbols else ""
            if not start or not end:
                rows = []
            else:
                dfs = [self.pull_minute(sym, freq, f"{s} 09:00:00", f"{e} 15:00:00")
                       for s, e in split_minute_range(start, end, freq)]
                df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
                # 迁移期特征开关（29 §六）：preserve=False 时启用竞价条并入首根（批 58b）
                if not getattr(req, "preserve_current_normalization", True):
                    from src.data_platform.adapters.tushare_adapter import merge_auction_into_first
                    df = merge_auction_into_first(df)
                rows = self.to_bar_rows(df, freq)
        elif kind in POOL_FETCH_KINDS:
            # 批 83b：池内深度数据（三档二档 10 表）收编 fetch 契约——sub_kind=**表名**
            # （归置键，与 sync_kind_config 0094/0106 的 sync_id 同词），列形状由
            # POOL_TABLE_SPECS 声明（非 bar 族不套 11 字段，见 contract.to_contract 两条形状律）。
            # 引擎侧只给 (kind, sub_kind, symbols, range_)；源 API/参数/窗口形态全在本层。
            spec = POOL_TABLE_SPECS.get(sub or "")
            if spec is None:
                raise UnsupportedFeature(f"tushare 未声明池内表 sub_kind={sub!r}")
            if spec["kind"] != kind:
                # 归置行 kind 与 adapter 声明漂移（test_pool_specs 断言守门）——运行时也响亮
                # 拒绝而非就近拉数：错族拉取会往错表写数（防串源的形状维）
                raise UnsupportedFeature(
                    f"tushare 池内表 {sub} 属 kind={spec['kind']}，请求 kind={kind} 不符")
            sym = req.symbols[0] if req.symbols else ""
            df = pull_pool_table(spec, sym, start, end, pro=self.get_client())
            rows = to_pool_rows(spec, df, sym)
            return to_contract(rows, source=self.provider, kind=kind, columns=spec["columns"])
        else:
            raise UnsupportedFeature(f"tushare 未实现 fetch(kind={kind}, sub_kind={sub})")
        return to_contract(rows, source=self.provider, kind=kind, freq=freq)

    def fetch_supply(self, kind: str, sub_kind: str | None = None, **params) -> pd.DataFrame:
        """批 100：供给面 `(kind, sub_kind)` → 源侧拉取（全市场日均 / 快照 / 全量重建）。

        现状：本端口先收编引擎里 **9 个 `importlib.import_module("…tushare_adapter")` 硬编码**
        的工厂项（tier1 逐日 7 + 全量重建 2）——使 `sync_config.provider` 对这些项生效、写入
        侧可换源（第二 adapter 实现同族 `fetch_supply` 即可，引擎零改动）。
        分派与参数按 `_SUPPLY_PULL` 声明；`params` 透传源侧窗口参数（`trade_date`/`ann_date`）。
        """
        from src.data_platform.adapters import tushare_adapter as ta
        fn_name = _SUPPLY_PULL.get((kind, sub_kind))
        if fn_name is None:
            raise UnsupportedFeature(
                f"tushare 未实现 fetch_supply(kind={kind}, sub_kind={sub_kind})")
        fn = getattr(ta, fn_name)
        # 只透传源函数真正接受的参数（如 namechange 不吃 trade_date、concept 吃）——
        # 让引擎对同族 kind 用统一调用形态（都传 trade_date），adapter 侧各取所需。
        import inspect
        _accepts = set(inspect.signature(fn).parameters)
        return fn(**{k: v for k, v in params.items() if k in _accepts})


# 批 100：供给面 `(kind, sub_kind)` → tushare_adapter 的 pull 函数名。
# 值域口径：tier1 逐日表（全市场单日）+ 全量重建表（快照）。`(kind, sub_kind)` 归置真相在
# `sync_kind_config`（0094/0106）——本表只声明「源侧怎么拉」，由 test_batch100 与归置行对账（漂移即红）。
_SUPPLY_PULL: dict[tuple[str, str | None], str] = {
    ("stk_limit", None): "pull_stk_limit",
    ("featured_daily", "moneyflow"): "pull_moneyflow",
    ("featured_daily", "margin_detail"): "pull_margin_detail",
    ("featured_daily", "top_list"): "pull_top_list",
    ("featured_daily", "block_trade"): "pull_block_trade",
    ("featured_daily", "cyq_perf"): "pull_cyq_perf",
    # 批 117：ST 官方名单快照（同族「按日全市场快照」——步 0 实测 pro.stock_st 全量 201 行）
    ("featured_daily", "st_list"): "pull_stock_st",
    ("financial_stmt", "forecast"): "pull_forecast",
    ("static_list", "namechange"): "pull_namechange",
    ("industry_class", "concept"): "pull_concept",
    # 批 107：7 个字面量供给项收编（原 `_get_pro(prov).xxx` / 直连 pull_* ⇒ 经 adapter）。
    # `static_list` 四子类共用 `pull_stock_basic_raw` / `pull_cb_basic_raw` 两个壳（靠 params 区分：
    # fields= 取三列 / ts_code= 取单只），归置键 `(kind, sub_kind)` 仍一項一键（真源 sync_kind_config）。
    ("fundamental_daily", None): "pull_daily_basic_raw",       # astock_basic
    ("static_list", "stock"): "pull_stock_basic_raw",          # astock_list
    ("static_list", "symbols"): "pull_stock_basic_raw",        # static_symbols
    ("static_list", "convertible"): "pull_cb_basic_raw",       # cb_basic（全量）
    ("static_list", "terms"): "pull_cb_basic_raw",             # convertible_terms（逐只 ts_code=）
    ("static_list", "etf"): "pull_fund_basic_raw",             # etf_list
    ("trade_cal", None): "pull_trade_cal_raw",                 # trade_cal（逐年，纯读）
}


# 批 103b：`JoinQuantAdapter` 已从本文件迁出至 `joinquant_adapter.py`（真接实现 + jqdatasdk
# 形状知识）。注册仍靠 `@register_adapter` 的 import 副作用——`adapters/__init__.py` 显式
# import 该模块，否则 adapter 静默缺席（`get_adapter` 抛 ValueError → engine 回落 tushare 拉错源）。


@register_adapter
class RiceQuantAdapter(BaseDataAdapter):
    """米筐 adapter（stub；真接需 rqdatac + 字段映射，total_turnover 元）。"""
    provider = "ricequant"

    def pull_daily(self, symbol, start, end, adj=None, kind="astock"):
        raise NotImplementedError("米筐 adapter stub：真接需 rqdatac")

    def pull_minute(self, symbol, freq, start, end):
        raise NotImplementedError("米筐 adapter stub：真接需 rqdatac")

    def to_bar_rows(self, df, freq, adj_map=None):
        raise NotImplementedError("米筐 adapter stub：真接需 rqdatac")


def _log_bar_quality_local(df, label: str) -> None:
    """批 72 质量校验迁移面（fail-soft+一行一 issue——语义照抄 engine._log_bar_quality；
    下层（data_platform）禁 import 上层（data_sync），复制语义不调函数，test_layering 守门）。
    空 df 闸在调用侧（v2 #2 双同：节假日/非交易区间不喷噪声）；label 沿用批 71 词表
    （bar_daily/etf_daily/cb_daily——观察期查询零改动，v2 #6 仲裁）。"""
    import logging
    try:
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(df)
        for issue in q.get("issues") or []:
            logging.getLogger(__name__).warning("[quality] %s %s", label, issue)
    except Exception as e:
        logging.getLogger(__name__).warning("[quality] %s 校验器异常（不阻入库）: %s", label, e)

