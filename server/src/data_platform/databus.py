"""批 59·M4：DataBus 消费门面（28 §九 / 29 §七）。

核心语义（28 §7.1/7.2）：「仓即候选①，miss 兜长尾」——消费模式 local_pg 恒第一候选
（db.get_bars 包装），DataGap 时远端 fetch-on-miss（chain.fetch skip local_pg）落仓
（store.save 带版本）。get/get_snapshot/subscribe 本批为骨架（snapshot 缓存 + subscribe
真实实现留 M5）。

只切拉型（get_bars/get）；不动交易（M6）。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from .store import Store

logger = logging.getLogger("data_platform.databus")

_SNAPSHOT_TTL = 5  # 28 §7.4 快照缓存 TTL（秒）


class StreamHandle:
    """subscribe 占位句柄（M5 流协议接真实现）。"""

    def __init__(self, sub):
        self.subscription = sub
        self.active = False

    def close(self):
        self.active = False


class DataBus:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()

    # —— 拉型主入口：bar 序列 ——
    def get_bars(self, req):
        """local_pg 首候选 → DataGap 则 fetch-on-miss → (frame, watermark)。

        resolve 只在 miss 时发生（local 命中零路由依赖——不读 external_interface/路由表，
        避免本地热路径被路由表/DB 抖动拖垮，批 59 双盲审 P1）。
        """
        from src.quant_common.contract import DataGap
        try:
            frame = self._local_fetch(req)
        except DataGap:
            frame = self._fetch_on_miss(req)
        return frame, self._watermark(frame)

    def _fetch_on_miss(self, req):
        """本地空→远端 fetch-on-miss（28 §7.2「兜长尾冷门」）。

        本批 adapter.fetch 的 bar 族只实现「同步引擎口径」（逐日批/区间，29 §六 决策1），未实现
        「消费面口径」（per-symbol 区间）——直接 fetch 会 UnsupportedFeature 或数据范围错
        （按日全市场 vs 单标的区间）。故本批降级空帧 + 告警；per-symbol 区间拉取留后续批
        （挂账：adapter 补 consume 口径 fetch）。
        """
        from src.quant_common.contract import to_contract
        logger.warning("fetch-on-miss 未接（adapter 无 per-symbol 区间拉取，挂账）: %s %s",
                       req.kind, req.symbols)
        return to_contract([], source="local_pg", kind=req.kind, freq=req.freq)

    # —— 参考数据统一入口（无 local_pg 特判——fundamental/featured 等）——
    def get(self, kind, req):
        from src.data_platform import routing
        from src.data_platform.adapters.base import get_adapter
        chain = routing.resolve(req)
        fetch_fn = lambda c: get_adapter(c.adapter).fetch(req, acct=c.account)
        return chain.fetch(fetch_fn, req)

    # —— 快照：Valkey TTL + SET NX 写者选举（骨架，28 §7.4；实际读取留后续）——
    def get_snapshot(self, req):
        """快照缓存骨架：键含 consumer/kind/symbols，TTL 5s；miss 时 SET NX 占位写者选举。

        本批返回 fetch 结果 + 简单缓存（不序列化 ContractFrame 到 Valkey——快照读取方未接，
        缓存命中/反序列化留 M5/消费方接入时补）。
        """
        from src.data_platform import routing
        from src.data_platform.adapters.base import get_adapter
        chain = routing.resolve(req)
        fetch_fn = lambda c: get_adapter(c.adapter).fetch(req, acct=c.account)
        return chain.fetch(fetch_fn, req)

    # —— 订阅：占位句柄（M5 流协议）——
    def subscribe(self, sub, sink=None):
        return StreamHandle(sub)

    # —— 内部 ——
    def _local_fetch(self, req):
        """local_pg 候选：db.get_bars → 11 字段 tuple → to_contract；空抛 DataGap。"""
        from src.data_platform.db import get_bars
        from src.quant_common.contract import BAR_COLUMNS, DataGap, to_contract
        symbol = req.symbols[0] if req.symbols else ""
        start = req.range_[0] if req.range_ else None
        end = req.range_[1] if req.range_ else None
        df = get_bars(symbol, req.freq, start, end)
        if df.empty:
            raise DataGap(f"local_pg 无 {req.kind} 数据: {symbol} {req.freq}")
        # to_dict("records") 对齐 db.get_bars 消费方的字段类型（NaN→Python float，非 numpy）
        rows = [tuple(r[c] for c in BAR_COLUMNS) for r in df.to_dict("records")]
        return to_contract(rows, source="local_pg", kind=req.kind, freq=req.freq)

    @staticmethod
    def _watermark(frame) -> datetime | None:
        """骨架版水位线=最大 ts。⚠️ 契约 Watermark=「最新连续无缺 ts（非最大 ts）」（contract.py），
        连续无缺判定留 M5 流协议补齐——M5 subscribe 续流前不得按本返回值当连续水位用。"""
        if not frame.rows:
            return None
        return max((r[2] for r in frame.rows if r[2] is not None), default=None)
