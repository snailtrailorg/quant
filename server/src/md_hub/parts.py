"""共享行情 Hub 数据面部件（批 2 迁移配套，2026-08-25）。

主循环迁上 strategy_framework/runtime 骨架时，为满足 main.py 行数预算
（docs/任务/批2-runtime骨架与hub首迁.md 验收 3），与主循环无涉的数据面部件
**原样移驻**本模块——代码零改动，仅位置变化；MinuteAggregator/_write_latest_tick
等在 main.py 顶部 import 重导出，既有测试导入路径（test_hub_arch/test_stock_detail）
不受影响。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger("md_hub")

try:
    from vnpy.trader.gateway import BaseGateway
except ImportError:   # 与 main 的 EventEngine 守卫同款：无 vnpy 环境可导入本模块（main 启动时统一报错退出）
    BaseGateway = object

LEASE_KEY = "hub:lease"
GEN_KEY = "hub:gen"
SURRENDER_KEY = "hub:surrender"
LATEST_TICK_PREFIX = "hub:latest_tick:"   # 三档项 12：详情页实时快照（tick 自带五档，U-2 修正 #2 零订阅变化）
LATEST_TICK_TTL = 65                      # 断流 65s 自动过期——详情页不展示陈旧价，降级腾讯/DB
PG_FLUSH_INTERVAL = 10.0      # bar 落库批量间隔（独立线程，R-BR7 不反压分发）
PG_QUEUE_MAX = 5000           # 落库缓冲上限（溢出丢最旧+告警，有界保证）


def _project_symbol(tick) -> str:
    """vnpy vt 后缀 → 项目后缀（SSE→SHSE），bar 表/流命名统一项目口径。"""
    ex = tick.exchange.value if tick.exchange else ""
    return f"{tick.symbol}.{'SHSE' if ex == 'SSE' else ex}"


def _in_bar_session(t: datetime) -> bool:
    """分钟 bar 聚合喂入门（P2 双轨修复批 2026-08-28，双轨四分类②④）。

    09:30:00 起喂（含）、11:30:00 起滤（修后 11:30 孤儿桶/伪 bar 不再产）、13:00 起喂、
    15:00:xx 含（收盘竞价快照必须喂进 [15:00] 桶）、15:01:00 起滤（后到竞价快照=当日缺根，
    盲审 A-P2-5 已知边界）。盘前快照（仿真源 09:26~09:30）不喂 → 冷启动基线顺延至
    09:30 后首笔——与 vnpy BarGenerator 首笔建基线语义一致化。
    盲审 A-P1-2/B-P2-3 钉死：仅作用于 agg.on_tick 喂入；stats/counters/_write_latest_tick
    （详情页盘前快照）一律不受影响。
    """
    hm = t.hour * 100 + t.minute
    return (930 <= hm < 1130) or (1300 <= hm < 1501)


class MinuteAggregator:
    """tick → 分钟 bar（分钟末标注，Tushare 口径）。

    - 桶 [10:00,10:01) → ts=10:01:00
    - volume/amount = 桶末累计 − 上桶末累计（XTP qty 当日累计语义，评审 S3）
    - untrusted：tick_count==0 不可能（无 tick 不成桶）；跨度<50% 且 tick_count<3 双门限（防低活跃误报）
    """

    def __init__(self):
        self._cur_date = None       # 评审 C4：交易日翻转检测
        self._buckets: dict[str, dict] = {}
        self._last_acc: dict[str, tuple] = {}  # symbol -> (volume_acc, amount_acc) 上桶末累计
        self._last_tick_ts: dict[str, float] = {}

    def on_tick(self, symbol: str, tick) -> Optional[dict]:
        """喂 tick；跨分钟时 finalize 上一桶并返回 bar dict，否则 None。"""
        if not tick.last_price or tick.last_price <= 0:   # B4：异常快照价 0 过滤（vnpy 同款）
            return None
        self._last_tick_ts[symbol] = time.time()
        t = tick.datetime
        d = t.date()
        if self._cur_date is None:
            self._cur_date = d
        elif d != self._cur_date:
            # 评审 C4：XTP qty 当日累计——跨交易日必须清累计基线，否则早盘 volume 恒 0
            self._cur_date = d
            self._buckets.clear()
            self._last_acc.clear()
        b = self._buckets.get(symbol)
        if b is None:
            # 冷启动基线（2026-08-17 晚实测缺陷：首个桶无上桶基线会把"当日累计全量"当桶内增量——
            # 首见 tick 的累计值设为基线，首桶只计其后增量，与 vnpy 首tick建基线语义一致）
            if symbol not in self._last_acc:
                self._last_acc[symbol] = (tick.volume or 0.0, getattr(tick, "turnover", 0) or 0.0)
            self._buckets[symbol] = {
                "minute": t.replace(second=0, microsecond=0), "open": tick.last_price, "high": tick.last_price,
                "low": tick.last_price, "close": tick.last_price,
                "vol_acc": tick.volume or 0.0, "amt_acc": getattr(tick, "turnover", 0) or 0.0,
                "first_tick": t, "last_tick": t, "count": 1,
            }
            return None
        if t.replace(second=0, microsecond=0) == b["minute"]:
            b["high"] = max(b["high"], tick.last_price)
            b["low"] = min(b["low"], tick.last_price)
            b["close"] = tick.last_price
            b["vol_acc"] = tick.volume or 0.0
            b["amt_acc"] = getattr(tick, "turnover", 0) or 0.0
            b["last_tick"] = t
            b["count"] += 1
            return None
        # 新分钟 → finalize 上一桶
        bar = self._finalize(symbol, b)
        self._buckets[symbol] = {
            "minute": t.replace(second=0, microsecond=0), "open": tick.last_price,
            "high": tick.last_price, "low": tick.last_price, "close": tick.last_price,
            "vol_acc": tick.volume or 0.0, "amt_acc": getattr(tick, "turnover", 0) or 0.0,
            "first_tick": t, "last_tick": t, "count": 1,
        }
        return bar

    def flush_minute(self, minute_slot: int) -> list[dict]:
        """分窗 finalize（main._flush 三窗用）：只收 b["minute"] 时分数==minute_slot 的桶。

        P2 修复批（2026-08-28）替代 flush_all 的分窗版：收盘 15:00:0x 快照开 [15:00] 桶后，
        半熟桶须等竞价快照聚齐（15:01 窗）才 finalize——原 flush_all 在 15:00:05 无差别收，
        把只含一笔、不含竞价量的 [15:00] 桶落库（V=0，双轨四分类③）。
        pop 语义幂等（盲审 B-P1-1：宽窗内多次执行不重复产出）；tick 路径已 finalize 的桶
        早已 pop，天然无重复。
        """
        bars = []
        for symbol in list(self._buckets.keys()):
            b = self._buckets.get(symbol)
            if b and b["minute"].hour * 60 + b["minute"].minute == minute_slot:
                del self._buckets[symbol]
                bars.append(self._finalize(symbol, b))
        return bars

    def flush_rest(self) -> list[dict]:
        """日终兜底（15:01 窗 flush_minute 后调用，代码盲审 A-P2-b）：分窗化后滞留的
        陈旧桶（盘中断流标的尾桶，如 [11:25] 后无 tick）当日收口，防次日 C4 跨日清桶
        丢根；迟收值不变（untrusted 双门限照常标记稀疏）。"""
        bars = []
        for symbol in list(self._buckets.keys()):
            b = self._buckets.pop(symbol, None)
            if b:
                bars.append(self._finalize(symbol, b))
        return bars

    def flush_symbol(self, symbol: str) -> Optional[dict]:
        """单标的退订前 flush（2026-08-20 退订机制配套）：防丢在桶最后一分钟。"""
        b = self._buckets.pop(symbol, None)
        return self._finalize(symbol, b) if b else None

    def _finalize(self, symbol: str, b: dict) -> dict:
        from datetime import timedelta
        prev = self._last_acc.get(symbol)
        volume = max(0.0, b["vol_acc"] - (prev[0] if prev else 0.0))
        amount = max(0.0, b["amt_acc"] - (prev[1] if prev else 0.0))
        self._last_acc[symbol] = (b["vol_acc"], b["amt_acc"])
        span = (b["last_tick"] - b["first_tick"]).total_seconds()
        # 评审 S6：收盘/午休末桶（11:29/14:59 起）按构造稀疏——豁免双门限，防每日误冻结；
        # 15:00 桶同豁免（P2 修复批盲审 A-P1-1：仅 1~2 笔竞价快照，不豁免则每日 15:01 根
        # untrusted=True 被消费方滤丢收盘竞价根）
        closing = b["minute"].hour * 60 + b["minute"].minute in (11 * 60 + 29, 14 * 60 + 59, 15 * 60)
        untrusted = (not closing) and span < 30 and b["count"] < 3   # 双门限（评审）
        return {
            "symbol": symbol,
            "ts": b["minute"] + timedelta(minutes=1),   # 分钟末标注（R-BR9 Tushare 口径）
            "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"],
            "volume": volume, "amount": amount, "tick_count": b["count"],
            "untrusted": untrusted,
        }


class _PGWriter(threading.Thread):
    """bar 批量落库（独立线程，有界队列，R-BR7/B7）。影子期写 bar_hub（F2）。"""

    def __init__(self):
        super().__init__(daemon=True, name="hub-pg-writer")
        self.q: list[dict] = []
        self.lock = threading.Lock()
        self.dropped = 0

    def push(self, bar: dict) -> None:
        with self.lock:
            if len(self.q) >= PG_QUEUE_MAX:
                self.q.pop(0)
                self.dropped += 1
            self.q.append(bar)

    def run(self) -> None:
        while True:
            time.sleep(PG_FLUSH_INTERVAL)
            with self.lock:
                batch, self.q = self.q, []
            if not batch:
                continue
            try:
                from src.data_platform.db import get_conn
                with get_conn() as conn:
                    for b in batch:
                        conn.execute(
                            "INSERT INTO bar_hub (symbol, ts, open, high, low, close, volume, amount, untrusted) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                            "ON CONFLICT (symbol, ts) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, "
                            "low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume, "
                            "amount=EXCLUDED.amount, untrusted=EXCLUDED.untrusted",
                            (b["symbol"], b["ts"], b["open"], b["high"], b["low"], b["close"],
                             b["volume"], b["amount"], b.get("untrusted", False)))
                    conn.commit()
            except Exception as e:
                logger.warning("bar_hub 批量落库失败（%d 条，丢弃）: %s", len(batch), e)


def _lease_acquire(r) -> tuple[bool, str, int]:
    """租约 + 代次（R-DL4）。返回 (ok, uuid, gen)。区分 Valkey 不可达与 NX 失败（评审陷阱 8）。"""
    import secrets
    uuid_ = secrets.token_hex(8)
    try:
        got = r.set(LEASE_KEY, uuid_, nx=True, ex=30)
    except Exception as e:
        logger.error("租约存储不可达（重试，不退出）: %s", e)
        return False, uuid_, 0
    if not got:
        try:
            holder = r.get(LEASE_KEY)
        except Exception:
            holder = "?"
        logger.error("租约被持有（%s），本实例让位退出", holder)
        return False, uuid_, -1   # -1 = 真让位（surrender），0 = 网络问题稍后重试
    try:
        gen = int(r.incr(GEN_KEY))
    except Exception as e:
        logger.error("gen 计数器不可达: %s", e)
        return False, uuid_, 0
    return True, uuid_, gen


def _lease_boot(r) -> tuple[str, int]:
    """启动租约获取（先拿权再连行情）：3 次重试；真让位 SystemExit(3)，重试耗尽 os._exit(4)。"""
    for attempt in range(3):
        ok, my_uuid, gen = _lease_acquire(r)
        if ok:
            return my_uuid, gen
        if gen == -1:   # 真让位：写标记退出，unit 的 StartLimit 会接管
            try:
                r.set(SURRENDER_KEY, datetime.now().isoformat(), ex=600)
            except Exception:
                pass
            raise SystemExit(3)
        time.sleep(5)
    os._exit(4)


_LEASE_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


def _write_latest_tick(r, symbol: str, tick, fail_ts: dict) -> None:
    """三档项 12：最新 tick 快照落 Valkey（价量+五档+涨跌停，TTL 65s）。

    O 盲审修正：字段名 limit_up/limit_down（vnpy TickData 实名，非 upper_limit）；
    0 价过滤前置（与 agg B4 同款，防竞价 0.00 上屏）；连续失败 60s 退避
    （Valkey 半死时防每 tick 3s 阻塞拖死 tick→bar 主链）。
    模块级（可单测——闭包形态曾让字段名错误零覆盖藏身，O 审 S1）。
    """
    if not tick.last_price or tick.last_price <= 0:
        return
    now = time.time()
    if symbol in fail_ts and now - fail_ts[symbol] < 60:
        return   # 退避窗口内跳过（连败后不再每 tick 撞 Valkey）
    try:
        r.set(LATEST_TICK_PREFIX + symbol, json.dumps({
            "ts": tick.datetime.isoformat() if tick.datetime else None,
            "name": getattr(tick, "name", ""),
            "last": tick.last_price,
            "open": tick.open_price, "high": tick.high_price, "low": tick.low_price,
            "pre_close": tick.pre_close,
            "upper_limit": tick.limit_up, "lower_limit": tick.limit_down,
            "volume": tick.volume, "amount": getattr(tick, "turnover", 0) or 0.0,
            "bid": [tick.bid_price_1, tick.bid_price_2, tick.bid_price_3, tick.bid_price_4, tick.bid_price_5],
            "bid_v": [tick.bid_volume_1, tick.bid_volume_2, tick.bid_volume_3, tick.bid_volume_4, tick.bid_volume_5],
            "ask": [tick.ask_price_1, tick.ask_price_2, tick.ask_price_3, tick.ask_price_4, tick.ask_price_5],
            "ask_v": [tick.ask_volume_1, tick.ask_volume_2, tick.ask_volume_3, tick.ask_volume_4, tick.ask_volume_5],
        }), ex=LATEST_TICK_TTL)
        fail_ts.pop(symbol, None)
    except Exception as e:
        fail_ts[symbol] = now
        logger.debug("latest_tick 写失败 %s: %s", symbol, e)


class ThinGateway(BaseGateway):
    """仅事件转发；7 个抽象方法全量 stub（hub 数据面永不交易，R-HALT1 代码级保证）。"""

    def connect(self, setting: dict) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def subscribe(self, req) -> None:
        self.md_api.subscribe(req)

    def send_order(self, req) -> str:
        raise NotImplementedError("hub 数据面禁用")

    def cancel_order(self, req) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def query_account(self) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def query_position(self) -> None:
        raise NotImplementedError("hub 数据面禁用")

    def close(self) -> None:
        pass
