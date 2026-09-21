"""批 56a·M0 契约层：数据供给总线全部跨模块类型（28 号蓝图 §三/§十一 的可执行化）。

定位（29 号 §三）：纯类型+纯函数校验，零业务逻辑/零 DB 访问/零注册表实现。
此后每批被本模块钉守着，走形即红（文档契约只是类型的注释）。

分层映射（五层↔项目四层——29 号 §三交付物附录）：
| 蓝图层         | 本仓落位                                    |
|----------------|---------------------------------------------|
| L1 契约        | quant_common（本文件）                      |
| L2 adapter+L3 路由+S 存储 | data_platform（adapters/routing/db） |
| L4 门面        | DataBus 契约在 quant_common、实现在 data_platform；TradeBus 执行链在 strategy_framework |
| L5 消费        | 各消费方（web_api/策略框架/回测）           |

术语速查见 28 号文首；DataKind 词数=26（行情 7+参考 10+事件 2+交易 4+占位 3）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, TypedDict

# ─────────────────────────── 品类与时态（28 §十一） ───────────────────────────

DataKind = Literal[
    # 行情（7）
    "bar_daily", "bar_minute", "index_daily", "snapshot",
    "stream_bar", "stream_tick", "depth",
    # 参考数据（10）
    "static_list", "trade_cal", "index_constituents", "industry_class",
    "fundamental_daily", "financial_stmt", "featured_daily",
    "stk_limit", "adj_factor", "funding_rate",
    # 事件（2）
    "suspend", "corporate_action",
    # 交易运行时（4）
    "account_query", "order_stream", "trade_exec", "settlement",
    # 占位（3——枚举治理：无消费不实装，先占位防字段歧义）
    "open_interest", "liquidation", "fx_rate",
]

DATA_KINDS: frozenset[str] = frozenset(DataKind.__args__)  # type: ignore[attr-defined]

Temporality = Literal["historical", "snapshot", "streaming"]
TEMPORALITIES: frozenset[str] = frozenset(Temporality.__args__)  # type: ignore[attr-defined]

# 合法组合表（28 §十一——kind→时态是映射非笛卡尔积；类型系统封死非法组合）
KIND_TEMPORALITY: dict[str, frozenset[str]] = {
    "bar_daily":      frozenset({"historical"}),
    "index_daily":    frozenset({"historical"}),
    "bar_minute":     frozenset({"historical", "streaming"}),   # hybrid：盘中流生成、盘后权威回补（28 §8.4）
    "snapshot":       frozenset({"snapshot"}),
    "stream_bar":     frozenset({"streaming"}),
    "stream_tick":    frozenset({"streaming"}),
    "depth":          frozenset({"streaming"}),
    "static_list":    frozenset({"historical"}),
    "trade_cal":      frozenset({"historical"}),
    "index_constituents": frozenset({"historical"}),
    "industry_class": frozenset({"historical"}),
    "fundamental_daily":  frozenset({"historical"}),
    "financial_stmt": frozenset({"historical"}),
    "featured_daily": frozenset({"historical"}),
    "stk_limit":      frozenset({"historical"}),
    "adj_factor":     frozenset({"historical"}),
    "funding_rate":   frozenset({"historical", "snapshot"}),    # 历史 + 当期预测费率
    "suspend":        frozenset({"historical", "snapshot"}),    # 事件史 + 当前状态查询
    "corporate_action": frozenset({"historical", "snapshot"}),
    "account_query":  frozenset({"snapshot", "streaming"}),     # 查询 + 推送
    "order_stream":   frozenset({"streaming"}),
    "trade_exec":     frozenset({"streaming"}),
    "settlement":     frozenset({"historical"}),
    "open_interest":  frozenset({"historical", "snapshot"}),
    "liquidation":    frozenset({"historical", "snapshot"}),
    "fx_rate":        frozenset({"historical", "snapshot"}),
}


def is_legal(kind: str, temporality: str) -> bool:
    """组合合法性纯函数（CI 断言三的消费点）。未知 kind 恒 False。"""
    ts = KIND_TEMPORALITY.get(kind)
    return ts is not None and temporality in ts


# ─────────────────────────── 错误三分类（28 §3.3） ───────────────────────────

class ContractError(Exception):
    """契约层错误基类。"""


class ParamError(ContractError):
    """请求本身错（未知 symbol/非法区间）→ 不 failover，直接抛。"""


class SourceUnavailable(ContractError):
    """源故障（超时/熔断/限速打满）→ 可 failover 到下一候选。"""


class DataGap(ContractError):
    """源正常但无此数据（停牌/未上市/覆盖外）→ 可降级/换覆盖更广候选。"""


# ─────────────────────────── 范围（29 §三——六审软件必问①立法） ───────────────────────────

@dataclass(frozen=True)
class Scope:
    """能力覆盖范围三元组——covers() 按 (market,exchange,category) ∈ scope 匹配（None=通配）。

    markets 立法非空（29 §三：frozenset[str]，仅 exchanges/categories 可 None=通配；
    全市场=显式列全集——防"跨市场通配"非法态被构造，盲审 A/B 契约加宽收回）。
    """
    markets: frozenset[str]                 # {'astock'} / {'crypto'}
    exchanges: frozenset[str] | None        # None=该市场全所（28 号 exchanges NULL 同义）
    categories: frozenset[str] | None       # None=全品类

    def __post_init__(self):
        if not self.markets:
            raise ValueError("Scope.markets 非空（29 §三）——全市场=显式列 frozenset")

    def covers_one(self, market: str, exchange: str, category: str) -> bool:
        """单标的匹配（M2 硬过滤消费；SM 解析标的三元组后调此）。"""
        if market not in self.markets:
            return False
        if self.exchanges is not None and exchange not in self.exchanges:
            return False
        if self.categories is not None and category not in self.categories:
            return False
        return True


ASTOCK_ALL = Scope(frozenset({"astock"}), None, None)
ASTOCK_SHSE_SZSE = Scope(frozenset({"astock"}), frozenset({"SHSE", "SZSE"}), None)  # stk_mins 无 BSE 的覆盖事实
CRYPTO_ALL = Scope(frozenset({"crypto"}), None, None)


# ─────────────────────────── 请求与事件（28 §3.1 + v3.2 mode 分叉） ───────────────────────────

class AsOf(Enum):
    """PIT 双口径（28 §3.4）：PIT=当时知道什么（回测防前视）/LATEST=现在知道什么（当前决策）。"""
    PIT = "pit"
    LATEST = "latest"


RequestMode = Literal["consume", "supply"]
# consume=消费模式（local_pg 恒第一候选+fetch-on-miss 兜长尾）
# supply=供给模式（同步任务进货：候选链排除 local_pg——仓=对账基准非货源；28 §7.1 v3.2）


@dataclass(frozen=True)
class DataRequest:
    """拉型请求统一形态（get/get_bars/get_snapshot 的内部表达）。"""
    kind: str
    symbols: tuple[str, ...]
    temporality: str
    sub_kind: str | None = None          # 聚合 kind 域判别（featured_daily/financial_stmt 域——29 §三）
    range_: tuple[datetime, datetime] | None = None   # None=全量/当期
    freq: str | None = None              # bar 族必填（"1D"/"1min"/…）
    as_of: AsOf = AsOf.LATEST            # 仅 pit 类 kind 生效
    deadline_ms: int = 10_000
    consumer_tag: str = "default"
    mode: RequestMode = "consume"


FetchRequest = DataRequest  # 语义别名（28 §3.1 沿用）


@dataclass(frozen=True)
class Subscription:
    """推型订阅。from_watermark=None 从现在起；有值=水位线重放（28 §8.3）。"""
    kind: str
    symbols: tuple[str, ...]
    from_watermark: datetime | None = None
    consumer_tag: str = "default"


Watermark = datetime  # 语义别名：该 kind×symbol 仓内最新连续无缺 ts（非最大 ts——中间可能有洞）


@dataclass(frozen=True)
class ContractEvent:
    """Sink 收到的流上统一信封（28 §3.1）。"""
    kind: str
    gen: int                  # 流世代（worker 检测跳变触发重暖机——28 §8.2）
    seq: int                  # gen 内单调序号
    payload: dict             # BarRow 展开行 / SnapshotRow / OrderEvent / AccountState
    ts_receive: datetime      # 总线接收时刻（延迟观测）


class SnapshotRow(TypedDict, total=False):
    """快照契约字段集（28 §3.1——M0 立为正式类型：CI 列白名单断言的执法对象）。

    crypto scope 必填 mark_price/index_price/funding_rate_next；astock 无关字段缺省 NULL。
    """
    vt_symbol: str
    ts: datetime
    last: float
    bid: float
    ask: float
    bid_vol: float
    ask_vol: float
    volume: float          # 当日累计
    amount: float
    mark_price: float      # crypto 必填——强平锚定（非最新成交价）
    index_price: float     # crypto——标记价的指数成分锚
    funding_rate_next: float  # crypto 当期预测费率
    limit_up: float
    limit_down: float


# ─────────────────────────── 数据形状（24 号 11 字段契约+列白名单） ───────────────────────────

# bar 契约 11 字段（顺序=to_bar_rows/save_bars 元组序；volume=股/币非手，amount=元/USDT）
BAR_COLUMNS: tuple[str, ...] = (
    "symbol", "freq", "ts", "open", "high", "low", "close",
    "volume", "amount", "adj_factor", "source",
)
ALLOWED_BAR_COLUMNS: frozenset[str] = frozenset(BAR_COLUMNS)
# CI 断言二的合法列全集（bar 族；snapshot 族=SnapshotRow.__annotations__）
# 禁复权价列（close_qfq 之类）——28 §3.4 复权立法：库中永远存不复权原始价+adj_factor 旁挂
ALLOWED_SNAPSHOT_COLUMNS: frozenset[str] = frozenset(SnapshotRow.__annotations__.keys())


# ─────────────────────────── 统一帧（28 §5.2 to_contract 产物） ───────────────────────────

@dataclass(frozen=True)
class ContractFrame:
    """fetch 返回的统一帧（28 §5.2 to_contract 产物——同步/消费共用）。

    rows=统一 11 字段行（BAR_COLUMNS 序，元组）；source=数据源 provider；
    fetched_at=摄取时间戳（UTC aware——28 §15.1 血缘链第二环）。
    """
    kind: str
    rows: tuple[tuple, ...]
    source: str
    freq: str
    fetched_at: datetime


def to_contract(rows, *, source: str, kind: str, freq: str, fetched_at: datetime | None = None) -> ContractFrame:
    """归一义务④（28 §5.2 结束时刻）：11 字段序校验 + ts UTC aware 校验 + 包帧。

    收编统一出口（29 §三 CI 断言二运行时执法）：任何 adapter 的 fetch 输出都过此关——
    rows 必须是 to_bar_rows 产物的 11 字段元组（BAR_COLUMNS 序），ts 必须 UTC aware；
    违者抛 ContractError（不 failover，请求/实现错）。
    """
    from datetime import timezone as _tz
    if fetched_at is None:
        fetched_at = datetime.now(tz=_tz.utc)
    for r in rows:
        if len(r) != 11:
            raise ContractError(f"{source} 输出非 11 字段（{len(r)}）: {r[:3]}")
        ts = r[2]
        if ts is None or ts.tzinfo is None or ts.utcoffset().total_seconds() != 0:
            raise ContractError(f"{source} 输出 ts 非 UTC aware: {ts!r}")
    return ContractFrame(kind=kind, rows=tuple(rows), source=source, freq=freq, fetched_at=fetched_at)


# ─────────────────────────── 注册表声明类型（28 §5.2） ───────────────────────────

@dataclass(frozen=True)
class CapabilityDecl:
    """adapter 能力声明单元（implements 列表元素）。"""
    kind: str
    temporality: str
    scope: Scope
    sub_kinds: frozenset[str] = frozenset()   # 聚合域声明其子类（如 featured_daily 声明五个 sub）
    pit: bool = False


@dataclass(frozen=True)
class Quality:
    """供应商质量档案（resolve 软排序+shadow 预算闸门输入）。"""
    latency: str            # "T+1 盘后" / "实时"
    coverage: float         # 0.0-1.0
    rate_profile: str       # 限速特征描述（派生 bulkhead 初值）
    cost: str               # "按次计积分" / "免费"


def validate_capability_decls(decls) -> list[str]:
    """声明集校验（CI 断言三核心——register_adapter 钩子调用；返回错误清单，空=合法）。

    校验项：kind 已知 / temporality 合法组合 / sub_kinds 非空当且仅当 kind 是聚合域
    （现状聚合域=featured_daily/financial_stmt/static_list/industry_class）。
    """
    aggregate_kinds = {"featured_daily", "financial_stmt", "static_list", "industry_class"}
    errors: list[str] = []
    for d in decls:
        if d.kind not in DATA_KINDS:
            errors.append(f"unknown kind: {d.kind}")
            continue
        if not is_legal(d.kind, d.temporality):
            errors.append(f"illegal combo: {d.kind}×{d.temporality}")
        if (d.kind in aggregate_kinds) != bool(d.sub_kinds):
            errors.append(f"sub_kinds mismatch: {d.kind} sub_kinds={sorted(d.sub_kinds)}")
    return errors
