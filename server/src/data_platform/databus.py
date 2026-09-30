"""批 59·M4：DataBus 消费门面（28 §九 / 29 §七）。

核心语义（28 §7.1/7.2）：「仓即候选①，miss 兜长尾」——消费模式 local_pg 恒第一候选
（db.get_bars 包装），DataGap 时远端 fetch-on-miss（chain.fetch skip local_pg）落仓
（store.save 带版本）。get/get_snapshot 为骨架（快照缓存读取留消费方接入时补）；
subscribe/水位线为 M5 真实现（批 60：回放 240+ts 截断+去重+未达降级，连续无缺网格锚）。

只切拉型（get_bars/get）；不动交易（M6）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .store import Store
from src.quant_common.redis_client import business_redis


logger = logging.getLogger("data_platform.databus")

_SNAPSHOT_TTL = 5  # 28 §7.4 快照缓存 TTL（秒）
_REPLAY_COUNT = 240   # 起播回放根数（≈1 交易日分钟 bar，hub STREAM_MAXLEN 5000 内）
_FREQ_BY_KIND = {"bar_minute": "1min", "bar_daily": "1D"}   # 未达降级 get_bars 的 freq 推断

_R = None

# 批 75·H7：fetch-on-miss 真拉范围=单标的 bar 族且本地读写路径对称的两种。
# index_daily 不在内——其本地仓=bar_index（读走 db.get_index_bars），db.get_bars 读不回，
# 接真拉=每次 miss 都重复打远端（落仓读不回），挂账「指数消费统一走 DataBus」时一并接。
_FETCH_ON_MISS_KINDS = frozenset({"bar_daily", "bar_minute"})


def _r():
    """Valkey 单例（routing/market_snapshot 同款）。"""
    global _R
    if _R is None:
        _R = business_redis(decode_responses=True, socket_timeout=3)
    return _R


def _msg_ts(m: dict) -> datetime | None:
    """流消息 ts 字段（ISO）→ aware datetime；坏值 None（截断/去重时丢弃）。"""
    try:
        return datetime.fromisoformat(m["ts"])
    except Exception:
        return None


def _next_minute_slot(t: datetime):
    """A 股分钟网格下一槽（分钟末标注：09:31~11:30 / 13:01~15:00）。

    午休锚 11:30→13:01、日终锚 15:00→None（跨日不要求连续——日界=期望缺口，
    水位线停在当日 15:00）。槽外时刻（脏数据）→ None。
    """
    from .tz import SHANGHAI
    local = t.astimezone(SHANGHAI)
    hm = local.hour * 60 + local.minute
    if hm == 11 * 60 + 30:                       # 午休锚
        nxt = local.replace(hour=13, minute=1)
    elif hm == 15 * 60:                          # 日终锚
        return None
    else:
        nxt = local + timedelta(minutes=1)
        nhm = nxt.hour * 60 + nxt.minute
        if not (9 * 60 + 31 <= nhm <= 11 * 60 + 30 or 13 * 60 + 1 <= nhm <= 15 * 60):
            return None                         # 槽外（竞价前/收盘后等）
    return nxt.astimezone(t.tzinfo)


def _is_slot(t: datetime) -> bool:
    """是否 A 股分钟合法槽（09:31~11:30 / 13:01~15:00）。竞价 9:26、午休 11:31~13:00 等槽外=False。"""
    from .tz import SHANGHAI
    hm = t.astimezone(SHANGHAI).hour * 60 + t.astimezone(SHANGHAI).minute
    return (9 * 60 + 31 <= hm <= 11 * 60 + 30) or (13 * 60 + 1 <= hm <= 15 * 60)


def _fetch_via_adapter(req):
    """adapter 取数工厂（盲审 B-P2-2：get_adapter 对无数据 adapter 的 provider（tencent/xtp 等
    rt_quote 类候选）抛 ValueError，不在 chain.fetch 的 SourceUnavailable 捕获集内——转
    SourceUnavailable 使链式下移而非整次 fetch 崩溃）。"""
    from src.data_platform.adapters.base import get_adapter
    from src.quant_common.contract import SourceUnavailable

    def fn(c):
        try:
            return get_adapter(c.adapter).fetch(req, acct=c.account)
        except ValueError as e:
            raise SourceUnavailable(f"{c.adapter} 无数据 adapter：{e}") from e
    return fn


class StreamHandle:
    """流订阅句柄（M5 真实现）：回放预取入 .bars，poll 增量 XREAD 续读。

    消息=14 号裸字段 dict（gen/seq/ts/pub_ts/untrusted/ohlc/volume/amount/tick_count，**全 str**，
    消费方自行转 float）；ContractEvent 信封化挂账 M7（29 §十一 v15 注记）。
    未达水位线时 .warmup=PG 降级帧（**含水位线那根**，与流「严格新于水位线」互补无重叠）。
    **fencing（stale_gen 拒旧 + gen 跳变重暖机）是消费方责任**——本句柄只按 ts 去重（同 ts 留新 gen，
    gen 跳变信号保留），不做 stale_gen 过滤。
    """

    def __init__(self, r, stream_key: str, sub):
        self.r = r
        self.stream_key = stream_key
        self.subscription = sub
        self.bars: list[dict] = []      # 回放根（时间正序，已 ts 截断+去重）
        self.warmup = None             # ContractFrame|None（回放未达水位线时的 PG 降级）
        self.last_id = "$"              # poll 起点（回放后=流内最新 id）
        self.active = True

    def poll(self, block_ms: int | None = None) -> list[dict]:
        """增量读一批（XREAD 自 last_id；失败告警返回空，不崩——消费方下次再试）。

        block_ms=None=非阻塞（默认，不拼 BLOCK）；传 ≥0 才阻塞（redis `BLOCK 0`=无限阻塞，勿用 0 表非阻塞）。
        消息字段全 str（decode_responses），消费方自行转 float；增量路径不去重，gen 过滤是消费方责任。
        """
        if not self.active:
            return []
        try:
            out = self.r.xread({self.stream_key: self.last_id}, count=500, block=block_ms)
        except Exception as e:
            logger.warning("XREAD %s 失败: %s", self.stream_key, e)
            return []
        bars = []
        for _key, entries in out or []:
            for _id, fields in entries:
                self.last_id = _id
                bars.append(dict(fields))
        return bars

    def close(self):
        self.active = False


class DataBus:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()

    # —— 拉型主入口：bar 序列 ——
    def get_bars(self, req):
        """local_pg 首候选 → DataGap 则 fetch-on-miss → (frame, watermark)。

        resolve 只在 miss 时发生（local 命中零路由依赖——不读配置行/路由表，
        避免本地热路径被路由表/DB 抖动拖垮，批 59 双盲审 P1）。
        """
        from src.quant_common.contract import DataGap
        try:
            frame = self._local_fetch(req)
        except DataGap:
            frame = self._fetch_on_miss(req)
        return frame, self._watermark(frame, getattr(req, "freq", None))

    def _fetch_on_miss(self, req):
        """本地空→远端 fetch-on-miss 真拉（28 §7.2「兜长尾冷门」；批 75·H7 落地）。

        范围=单标的 bar_daily/bar_minute 且带显式 range_——adapter 按 req.mode="consume"
        走 per-symbol 口径（base.py fetch 分派：bar_daily+stock/etf → pull_daily 单标的区间，
        不复权+逐行 adj_factor；bar_minute 本就 per-symbol）。链=resolve 后 chain.fetch
        skip local_pg（仓已知 miss，不再问一次）；拉到非空帧落仓（store.save，下次 local 命中）。
        其余情形维持旧降级空帧+告警（挂账可观测）：非 bar 族 consume 口径、多标的聚合（M7）、
        无显式区间（拉取窗不定）。拉取/落仓失败 fail-soft 降级空帧不崩。
        """
        from src.quant_common.contract import to_contract

        def _degrade(reason: str):
            logger.warning("fetch-on-miss 降级空帧（%s）: %s %s",
                           reason, req.kind, getattr(req, "symbols", ()))
            return to_contract([], source="local_pg", kind=req.kind, freq=req.freq)

        if req.kind not in _FETCH_ON_MISS_KINDS:
            return _degrade("kind 不在真拉范围（bar 族外挂账）")
        if len(req.symbols) != 1:
            return _degrade("多标的聚合未接（挂账 M7 同源）")
        if not req.range_ or req.range_[0] is None or req.range_[1] is None:
            return _degrade("无显式区间（拉取窗不定）")
        try:
            from src.data_platform import routing
            chain = routing.resolve(req)
            frame = chain.fetch(_fetch_via_adapter(req), req, skip=frozenset({"local_pg"}))
        except Exception as e:
            logger.warning("fetch-on-miss 远端拉取失败，降级空帧: %s %s: %s",
                           req.kind, req.symbols, e)
            return to_contract([], source="local_pg", kind=req.kind, freq=req.freq)
        if frame.rows:
            try:
                self.store.save(frame)
            except Exception as e:
                logger.warning("fetch-on-miss 落仓失败（帧照常返回）: %s", e)
        return frame

    # —— 参考数据统一入口（无 local_pg 特判——fundamental/featured 等）——
    def get(self, kind, req):
        from src.data_platform import routing
        chain = routing.resolve(req)
        return chain.fetch(_fetch_via_adapter(req), req)

    # —— 快照：Valkey TTL + SET NX 写者选举（骨架，28 §7.4；实际读取留后续）——
    def get_snapshot(self, req):
        """快照缓存骨架：键含 consumer/kind/symbols，TTL 5s；miss 时 SET NX 占位写者选举。

        本批返回 fetch 结果 + 简单缓存（不序列化 ContractFrame 到 Valkey——快照读取方未接，
        缓存命中/反序列化留 M5/消费方接入时补）。
        """
        from src.data_platform import routing
        chain = routing.resolve(req)
        return chain.fetch(_fetch_via_adapter(req), req)

    # —— 订阅：M5 真实现（28 §8.3 水位线衔接，批 60 方案集 §八）——
    def subscribe(self, sub, sink=None):
        """流订阅：单标的 per-account 流 `hub:bars:{account_id}:{symbol}` 回放+增量（批 66b 键化）。

        account_id 必填（sub.account_id——D26 账号级 hub，无裸键形态）。
        回放：XREVRANGE count=240 → 时间正序 → from_watermark 严格新于截断 → ts 去重
        （同 ts 留新 gen——切换交界 gen 跳变信号保留）。fencing（stale_gen 拒旧/重暖机）是消费方责任。
        未达断言：from_watermark 有值但回放空/窗头未接上水位线（剪尾/断流/有洞）→
        告警 + 降级 get_bars 补 PG 历史（handle.warmup 含水位线根，与流互补无重叠）。
        多标的聚合/sink 推送/精确 ts 寻址：挂账 M7（本批单标的轮询，多标的 fail-fast）。
        """
        r = _r()
        if len(sub.symbols) != 1:
            raise ValueError(f"subscribe 单标的订阅，收到 {len(sub.symbols)} 个标的（多标的聚合挂账 M7）: {sub.symbols}")
        if sub.account_id is None:
            raise ValueError("sub.account_id required——裸键形态已退役（批 66b，D26 账号级 hub）")
        symbol = sub.symbols[0]
        stream_key = f"hub:bars:{sub.account_id}:{symbol}"
        raw = r.xrevrange(stream_key, count=_REPLAY_COUNT)   # [(id, fields)] 新→旧
        handle = StreamHandle(r, stream_key, sub)
        handle.last_id = raw[0][0] if raw else "$"            # poll 起点=流内最新 id
        msgs = []
        for _id, fields in raw:
            m = dict(fields)
            ts = _msg_ts(m)
            if ts is None:
                continue                                      # 坏 ts 消息丢弃
            msgs.append((ts, m))
        msgs.sort(key=lambda x: x[0])                          # 正序（旧→新）
        wm = sub.from_watermark
        if wm is not None:
            if wm.tzinfo is None:                              # naive 水位线 → 按上海本地解释转 UTC aware
                from .tz import as_utc
                wm = as_utc(wm)
            msgs = [(ts, m) for ts, m in msgs if ts > wm]      # 严格新于水位线（截断）
        # ts 去重（同 ts 留 gen 更大者——切换交界旧代末根/新代重发同 ts 时，保留新代保 gen 跳变信号）
        by_ts: dict = {}
        for ts, m in msgs:
            cur = by_ts.get(ts)
            if cur is None or int(m.get("gen", 0)) > int(cur.get("gen", 0)):
                by_ts[ts] = m
        handle.bars = [m for _ts, m in sorted(by_ts.items(), key=lambda x: x[0])]
        if wm is not None:
            # 未达断言：回放空，或回放窗头未接上水位线（wm 与窗头之间有洞，MAXLEN 剪不断）→ 降级补 PG
            head = _msg_ts(handle.bars[0]) if handle.bars else None
            gap = head is not None and _next_minute_slot(wm) is not None and head != _next_minute_slot(wm)
            if not handle.bars or gap:
                logger.warning("流 %s 回放未达水位线 %s（剪尾/断流/窗头有洞），降级 get_bars 补 warmup",
                               stream_key, wm)
                handle.warmup = self._warmup_fallback(sub, wm)
        return handle

    def _warmup_fallback(self, sub, wm):
        """未达水位线降级：get_bars（local_pg）拉水位线起历史（freq 按 kind 推断）。

        range_=(wm, now)——end 不传 None（db.get_bars 的 end=None 落 SQL `ts<=NULL` 恒空，
        盲审 A 实测；now 覆盖「wm 到当下」的 PG 回补）。warmup 含 wm 那根，与流「严格新于」互补。
        """
        from datetime import timezone

        from src.quant_common.contract import DataRequest
        freq = _FREQ_BY_KIND.get(sub.kind, "1min")
        req = DataRequest(kind=sub.kind, symbols=tuple(sub.symbols), temporality="historical",
                          freq=freq, range_=(wm, datetime.now(timezone.utc)))
        frame, _wm = self.get_bars(req)
        return frame if frame.rows else None

    # —— 内部 ——
    def _local_fetch(self, req):
        """local_pg 候选：db.get_bars → 11 字段 tuple → to_contract；空抛 DataGap。"""
        from src.data_platform.db import get_bars
        from src.quant_common.contract import BAR_COLUMNS, DataGap, to_contract
        symbol = req.symbols[0] if req.symbols else ""
        start = req.range_[0] if req.range_ else None
        end = req.range_[1] if req.range_ else None
        df = get_bars(symbol, req.freq, start, end, source=getattr(req, "source", None))
        if df.empty:
            raise DataGap(f"local_pg 无 {req.kind} 数据: {symbol} {req.freq}")
        # to_dict("records") 对齐 db.get_bars 消费方的字段类型（NaN→Python float，非 numpy）
        rows = [tuple(r[c] for c in BAR_COLUMNS) for r in df.to_dict("records")]
        # 批 62a 帧级 source（B-P2-6 裁定）：行级 BAR_COLUMNS 第 11 字段 distinct 聚合——
        # 单值直通 str/多值逗号 join（替换原字面量 'local_pg'——血缘 62a 消费；空集回退字面量）
        srcs = sorted({str(r[10]) for r in rows if r[10]})
        frame_src = ",".join(srcs) if srcs else "local_pg"
        return to_contract(rows, source=frame_src, kind=req.kind, freq=req.freq)

    @staticmethod
    def _watermark(frame, freq=None) -> datetime | None:
        """连续无缺水位线（M5 实装，批 60）：正向扫，首缺口前的连续末端 ts。

        - freq="1min"：A 股分钟网格锚（`_next_minute_slot`——午休 11:30→13:01 衔接、日终 15:00 停）。
          起点=首个合法槽（跳过竞价 9:26 等槽外脏根）；**单交易日窗口内连续**——跨日连续
          （15:00→次日 9:31）需交易日历，挂账 market_hours（M1 有，本层未接）。
        - 其他 freq（日线等）：无交易日历网格，保持最大 ts 语义（挂账：daily 网格需 market_hours）。
        """
        if not frame.rows:
            return None
        tss = sorted({r[2] for r in frame.rows if r[2] is not None})
        if not tss:
            return None
        if len(tss) == 1 or freq != "1min":
            return tss[-1]
        start = next((i for i, ts in enumerate(tss) if _is_slot(ts)), None)
        if start is None:
            return tss[-1]
        wm = tss[start]
        for ts in tss[start + 1:]:
            nxt = _next_minute_slot(wm)
            if nxt is None or ts != nxt:
                break                            # 首缺口 → 水位线停在连续末端
            wm = ts
        return wm
