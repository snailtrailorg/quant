"""批 57·M2：路由内核（29 号 §五契约+28 号 §六实现真源）。

RoutingTable.resolve 五步：硬过滤（注册表能力+DB enabled+scope covers+权限）→
软排序（position 人工序+三因子加权，熔断沉底）→ bulkhead 闸门（fetch 时刻）→
审计采样 → 供给消费分叉（supply 排除 local_pg）。
CandidateChain.fetch=链式下移（SourceUnavailable 下移/DataGap 记尾注/全链尽抛）。

边界（29 号）：不实现 adapter fetch（fetch_fn 回调注入——M4 门面接线）；不管流（M5）；不管交易切换（M6）。
热更新：config_version=Valkey 单调整数（cfg:version）；本批=周期对账兜底（TTL 2s 惰性重建，
订阅 pub/sub 通道挂 M4/M5 接——29 号验收不考订阅）。
权限：principal=(username, role) 注入式——None=系统任务（supply 同步进程）全过；
local_pg 豁免（28 §6.1——本地仓无账号语义，权限写入侧已核）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field

from .db import get_conn

logger = logging.getLogger("data_platform.routing")

CFG_VERSION_KEY = "cfg:version"          # 热更新版本号（路由策略页保存时 INCR；各进程对账）
_RELOAD_TTL = 2.0                        # 周期对账兜底窗口（秒）
_BULKHEAD_TTL = 60                       # 计数键 TTL（防进程崩溃漏减——最坏 60s 自愈）
_BULKHEAD_DERIVED = {"tushare": 4}       # rate_profile 派生初值（R1 随 adapter 交付真实化——M3+）
_BULKHEAD_DEFAULT = 8

# local_pg 本地仓覆盖集（M2 试点=bar 族读路径；参考类 58+ 扩）
LOCAL_KINDS = frozenset({"bar_daily"})
_LOCAL_MARKETS = ("astock", "crypto")


def _r():
    """Valkey 单例（market_snapshot 同款）。"""
    global _R
    import redis
    if _R is None:
        _R = redis.Redis.from_url(
            os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True, socket_timeout=5)
    return _R


_R = None


@dataclass(frozen=True)
class Candidate:
    """resolve 产物的一档候选（29 号 §五）。account=external_interface 行快照 dict。"""
    adapter: str                    # provider 名（local_pg 为保留名）
    account: dict | None            # local_pg 无账号语义=None
    is_local: bool
    position: int = 0               # 人工序（Web 拖拽，小者先）
    quality: dict = field(default_factory=dict)   # 三因子档案（completeness/cost/latency 0~1；M3+ 真实化）


_LOCAL_CANDIDATE = Candidate(
    adapter="local_pg", account=None, is_local=True, position=-1,
    quality={"completeness": 0.9, "cost": 1.0, "latency": 1.0},   # 本地零成本零时延
)


@dataclass(frozen=True)
class CandidateChain:
    """resolve 结果（candidates 有序+配置世代号+世代指纹——审计 (fingerprint, epoch) 同代配对）。"""
    candidates: tuple[Candidate, ...]
    epoch: int
    fingerprint: str = ""

    def fetch(self, fetch_fn, req, skip: frozenset = frozenset()):
        """链式下移（29 号 §五立法）：①跳 skip 集；②SourceUnavailable 下移+failover 审计；
        ③bulkhead 超闸→skip_busy 审计+下移；④全链尽/超时→SourceUnavailable；
        ⑤DataGap→试后续候选，全 DataGap 则抛 DataGap。'无循环'=不回门面（on-miss 链内完成）。"""
        from src.quant_common.contract import DataGap, SourceUnavailable
        last_gap: Exception | None = None
        dm = getattr(req, "deadline_ms", None)
        dm = 10_000 if dm is None else dm          # None 兜 10s；0 保留=立即超时原义（or 会吞 0）
        deadline = time.monotonic() + max(dm, 0) / 1000.0
        for c in self.candidates:
            if c.adapter in skip:
                continue
            if time.monotonic() >= deadline:
                break
            if _breaker_open(c.adapter):     # 熔断源不占闸门（盲审 A/B 双 P1——先检后 admit 防计数泄漏）
                _audit("skip_unhealthy", req, self, self.candidates, skipped=c.adapter)
                continue
            if not _bulkhead_admit(c.adapter):
                _audit("skip_busy", req, self, self.candidates, skipped=c.adapter)
                continue
            try:
                return fetch_fn(c)
            except SourceUnavailable as e:
                _audit("failover", req, self, self.candidates, skipped=c.adapter)
                last_gap = None
                logger.warning("路由 failover：%s 不可用（%s），链下移", c.adapter, e)
            except DataGap as e:
                last_gap = e          # 记尾注：全链 DataGap 时如实抛
                logger.info("路由 DataGap：%s 无此数据，试覆盖更广候选", c.adapter)
            finally:
                _bulkhead_release(c.adapter)
        if last_gap is not None:
            raise last_gap
        raise SourceUnavailable(f"候选链全链尽或超时（{[c.adapter for c in self.candidates]}）")


def _bulkhead_admit(adapter: str) -> bool:
    """per-adapter 并发闸门（Valkey 计数——跨进程风暴同防，28 §6.1 v3）。

    被拒（超闸）时内部立即回减——被拒请求不占在途名额（否则计数虚高直至 TTL）。
    """
    if adapter == "local_pg":
        return True                        # 本地仓不设闸（无外部源可打）
    try:
        limit = _bulkhead_limit(adapter)
        key = f"routing:bh:{adapter}"
        n = _r().incr(key)
        if n == 1:
            _r().expire(key, _BULKHEAD_TTL)
        if n > limit:
            _r().decr(key)
            return False
        return True
    except Exception:
        return True                        # Valkey 故障=闸门放行（路由可用性优先于风暴防护）


def _bulkhead_release(adapter: str) -> None:
    if adapter == "local_pg":
        return
    try:
        _r().decr(f"routing:bh:{adapter}")
    except Exception:
        pass


def _bulkhead_limit(adapter: str) -> int:
    """阈值=routing_policy.bulkhead_defaults 覆写（多行取 min——保守聚合，盲审 B 跨进程一致性）
    > rate_profile 派生初值（M3+ 真实化）> 默认。"""
    st = get_table()
    hits = [row["bulkhead_defaults"][adapter] for row in (st.policy or {}).values()
            if isinstance(row.get("bulkhead_defaults"), dict)
            and isinstance(row["bulkhead_defaults"].get(adapter), int)]
    return min(hits) if hits else _BULKHEAD_DERIVED.get(adapter, _BULKHEAD_DEFAULT)


def _breaker_open(adapter: str) -> bool:
    """健康度回流（28 §6.4）：rate_limit 熔断注册表读状态——保护机制复用为决策输入。"""
    if adapter == "local_pg":
        return False
    from .rate_limit import _BREAKERS
    b = _BREAKERS.get(adapter)
    return bool(b) and b.state == "open"


class _TableState:
    """进程内 RoutingTable 状态（惰性重建+TTL 对账）。"""

    def __init__(self):
        self.policy: dict[str, dict] = {}
        self.rows: list[dict] = []
        self.version = 0
        self.loaded_at = 0.0
        self.fingerprint = ""
        self.lock = threading.Lock()

    def reload(self, rows: list[dict], policy: dict[str, dict], version: int):
        digest = hashlib.sha256(
            json.dumps([rows, policy], sort_keys=True, default=str).encode()).hexdigest()[:16]
        self.rows, self.policy, self.loaded_at = rows, policy, time.monotonic()
        self.fingerprint, self.version = digest, version


_STATE = _TableState()


def _load_state(force: bool = False) -> _TableState:
    version = 0
    try:
        v = _r().get(CFG_VERSION_KEY)
        version = int(v) if v and str(v).isdigit() else 0
    except Exception:
        pass
    if not force and _STATE.rows and (time.monotonic() - _STATE.loaded_at) < _RELOAD_TTL \
            and version == _STATE.version:
        return _STATE
    with _STATE.lock:
        if _STATE.rows and (time.monotonic() - _STATE.loaded_at) < _RELOAD_TTL \
                and version == _STATE.version:
            return _STATE
        rows, policy = [], {}
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT provider, market, exchanges, capabilities, params, position, enabled "
                "FROM external_interface ORDER BY position, id")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur = conn.execute("SELECT consumer_tag, weights, bulkhead_defaults FROM routing_policy")
            for tag, w, bh in cur.fetchall():
                policy[tag] = {"weights": w or {}, "bulkhead_defaults": bh}
        _STATE.reload(rows, policy, version)
    return _STATE


def get_table() -> _TableState:
    """RoutingTable 状态入口（热更新周期对账在此发生）。"""
    return _load_state()


def bump_config_version() -> int:
    """路由策略/接口配置变更时调用（保存端点）——各进程 TTL 对账感知。"""
    try:
        return int(_r().incr(CFG_VERSION_KEY))
    except Exception as e:
        logger.warning("cfg:version INCR 失败（对账退化为 TTL 兜底）: %s", e)
        return _STATE.version


def _weights_of(consumer_tag: str) -> dict:
    st = get_table()
    row = st.policy.get(consumer_tag) or st.policy.get("default") or {}
    return row.get("weights") or {"completeness": 0.5, "cost": 0.3, "latency": 0.2}


def _score(w: dict, quality: dict) -> float:
    """三因子加权（quality 档案缺项=中性 0.5；M3+ R1 交付义务真实化）。"""
    q = {k: float(quality.get(k, 0.5)) for k in ("completeness", "cost", "latency")}
    return (float(w.get("completeness", 0)) * q["completeness"]
            + float(w.get("cost", 0)) * q["cost"]
            + float(w.get("latency", 0)) * q["latency"])


def resolve(req, principal: tuple[str, str] | None = None) -> CandidateChain:
    """resolve 五步（28 §6.1 代码级真源）。principal=(username, role)；None=系统任务全过。"""
    from src.quant_common.contract import is_legal, ParamError
    from .security_master import SMClient
    st = get_table()
    w = _weights_of(getattr(req, "consumer_tag", "default"))
    mode = getattr(req, "mode", "consume")

    # 入口合法性（盲审 A/B P2）：请求本身错抛 ParamError 不进 failover 语义（28 §3.3）；
    # local_pg 同过此门（原绕检——盲审 B）；同时消除循环内重复调用
    if not is_legal(req.kind, req.temporality):
        raise ParamError(f"非法 kind×temporality 组合：{req.kind}×{req.temporality}")

    # covers 批量提级（盲审 A/B P2 N+1）：symbols 一次解析全行共用
    sm = SMClient()
    sym_set = tuple(req.symbols or ())

    cands: list[Candidate] = []
    # local_pg：仅消费模式恒第一候选（28 §7.1 v3.2——供给模式排除：仓=对账基准非货源）
    if mode != "supply" and req.kind in LOCAL_KINDS:
        cands.append(_LOCAL_CANDIDATE)
    _perm_cache: dict[str, bool] = {}   # market 去重（盲审 B——perms 无缓存直读 PG）
    for row in st.rows:
        if not row.get("enabled"):
            continue
        caps = row.get("capabilities") or []
        if isinstance(caps, str):
            try:
                caps = json.loads(caps)
            except (TypeError, ValueError):
                caps = []
        if req.kind not in caps and not _cap_covers(caps, req.kind):
            continue
        # scope covers：行 exchanges 限定（NULL=全所）×SM 解析标的档
        if not _row_covers(sm, row, sym_set):
            continue
        # 权限过滤（market 级——27 号 DECOMP 维度栈 M6+ 收敛；local_pg 豁免）
        if principal is not None:
            from .perms import market_op_allowed
            username, role = principal
            mkt = row.get("market", "")
            if mkt not in _perm_cache:
                _perm_cache[mkt] = market_op_allowed(username, role, mkt)
            if not _perm_cache[mkt]:
                continue
        cands.append(Candidate(adapter=row["provider"], account=row, is_local=False,
                               position=int(row.get("position") or 0)))
    # 排序键：健康一票前置（熔断源无论人工序多靠前都沉底——验收③字面；28 §6.1 样例将
    # health 并入 score，但 position 前置会抵消 −∞——张力裁决：健康>人工序>质量分）
    cands.sort(key=lambda c: (1 if _breaker_open(c.adapter) else 0, c.position, -_score(w, c.quality)))
    chain = CandidateChain(tuple(cands), epoch=st.version, fingerprint=st.fingerprint)
    _audit("resolve", req, chain, tuple(cands))
    return chain


# D25 v2：能力集 token → DataKind 覆盖（类粒度展开走 KIND_CAP_CLASS 派生——
# 旧 _CAP_KIND_ALIASES daily/minute 退役；kline 为 55a 历史别名保留兼容）
def _cap_covers(caps: list, kind: str) -> bool:
    from src.quant_common.markets import KIND_CAP_CLASS
    covers: set[str] = set()
    for c in caps:
        if c == "kline":                    # 55a 历史别名
            covers.add("bar_daily")
        elif c in ("hist_quote", "rt_quote", "trading", "ref_data", "inst_event"):
            covers |= {k for k, cls in KIND_CAP_CLASS.items() if cls == c}   # 类 token 展开全集
        else:
            covers.add(c)                   # 直接 DataKind 词（兼容）
    return kind in covers


def _row_covers(sm, row, symbols) -> bool:
    from src.quant_common.contract import Scope
    market = row.get("market") or ""
    markets = frozenset({market}) if market else frozenset(_LOCAL_MARKETS)
    exchanges = row.get("exchanges")
    scope = Scope(markets,
                  frozenset(exchanges) if isinstance(exchanges, (list, tuple)) and exchanges else None,
                  None)
    return sm.covers(scope, tuple(symbols or ()))


def _audit(cause: str, req, chain, cands, skipped: str | None = None) -> None:
    """审计落 routing_decision（M2 试点=全落；采样开关挂 M4 量级化时接 28 §15.3）。"""
    try:
        summary = {"kind": getattr(req, "kind", ""), "n_symbols": len(getattr(req, "symbols", ()) or ()),
                   "mode": getattr(req, "mode", "consume"), "consumer": getattr(req, "consumer_tag", "default")}
        names = [c.adapter for c in cands]
        if skipped:
            summary["skipped"] = skipped
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO routing_decision (fingerprint, req_summary, chain, epoch, cause) "
                "VALUES (%s,%s,%s::jsonb,%s,%s)",
                (_STATE.fingerprint, json.dumps(summary, ensure_ascii=False),
                 json.dumps(names), chain.epoch, cause))
            conn.commit()
    except Exception as e:
        logger.warning("路由审计落库失败（不阻断路由）: %s", e)
