"""共享行情 Hub 数据面部件（批 2 迁移配套，2026-08-25）。

主循环迁上 strategy_framework/runtime 骨架时，为满足 main.py 行数预算
（docs/obsolete/任务归档/批2-runtime骨架与hub首迁.md 验收 3），与主循环无涉的数据面部件
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

LEASE_KEY = "hub:lease"
GEN_KEY = "hub:gen"
SURRENDER_KEY = "hub:surrender"
INTENT_KEY = "hub:switch:intent"          # M5 切换意图 {snapshot, target}，EX 300（switch.py 写）
ACTIVE_INSTANCE_KEY = "hub:active_instance"   # M5 现任实例名（无 TTL），boot 仲裁
LATEST_TICK_PREFIX = "hub:latest_tick:"   # 三档项 12：详情页实时快照（tick 自带五档，U-2 修正 #2 零订阅变化）
LATEST_TICK_TTL = 65                      # 断流 65s 自动过期——详情页不展示陈旧价，降级腾讯/DB


def _key(base: str, account_id) -> str:
    """D6：键 account 化——A股 account_id=None 键不变（现状零回归），加密 per-account 加 :{account_id} 后缀。"""
    return f"{base}:{account_id}" if account_id is not None else base


def _project_symbol(tick) -> str:
    """vnpy vt 后缀 → 项目后缀（SSE→SHSE），bar 表/流命名统一项目口径。"""
    ex = tick.exchange.value if tick.exchange else ""
    return f"{tick.symbol}.{'SHSE' if ex == 'SSE' else ex}"


def _in_bar_session(t: datetime, market: str = "astock") -> bool:
    """分钟 bar 聚合喂入门（P2 双轨修复批 2026-08-28，双轨四分类②④）。

    09:30:00 起喂（含）、11:30:00 起滤（修后 11:30 孤儿桶/伪 bar 不再产）、13:00 起喂、
    15:00:xx 含（收盘竞价快照必须喂进 [15:00] 桶）、15:01:00 起滤（后到竞价快照=当日缺根，
    盲审 A-P2-5 已知边界）。盘前快照（仿真源 09:26~09:30）不喂 → 冷启动基线顺延至
    09:30 后首笔——与 vnpy BarGenerator 首笔建基线语义一致化。
    盲审 A-P1-2/B-P2-3 钉死：仅作用于 agg.on_tick 喂入；stats/counters/_write_latest_tick
    （详情页盘前快照）一律不受影响。
    D6 加密接入批：market="crypto" 恒喂（24/7，每分钟 bar）；market="astock" 用 A股时段。
    """
    if market == "crypto":
        return True
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

    def flush_stale(self, now: datetime) -> list[dict]:
        """加密 24/7 滞留桶收口：只 finalize 分钟已过去的桶（b["minute"] < 当前分钟），
        不碰当前分钟在途桶——flush_rest 的「pop 全部桶」日终语义在 crypto 24/7 下会截断
        活跃桶（同 symbol 同 ts 重复 bar，盲审 A/B P0）。"""
        cur_min = now.replace(second=0, microsecond=0)
        bars = []
        for symbol in list(self._buckets.keys()):
            b = self._buckets.get(symbol)
            if b and b["minute"] < cur_min:
                del self._buckets[symbol]
                bars.append(self._finalize(symbol, b))
        return bars

    def flush_symbol(self, symbol: str) -> Optional[dict]:
        """单标的退订前 flush（2026-08-20 退订机制配套）：防丢在桶最后一分钟。"""
        b = self._buckets.pop(symbol, None)
        return self._finalize(symbol, b) if b else None

    def _finalize(self, symbol: str, b: dict) -> dict:
        from datetime import timedelta
        from src.data_platform.tz import as_utc
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
            "ts": as_utc(b["minute"] + timedelta(minutes=1)),        # 分钟末标注（R-BR9 Tushare 口径）批 56b 起流协议 UTC 表示
            "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"],
            "volume": volume, "amount": amount, "tick_count": b["count"],
            "untrusted": untrusted,
        }


def _lease_acquire(r, instance_name: str = "", account_id=None) -> tuple[bool, str, int]:
    """租约 + 代次（R-DL4）。返回 (ok, uuid, gen)。区分 Valkey 不可达与 NX 失败（评审陷阱 8）。

    M5：uuid 运行时 token_hex（A/B 同，v15 砍 HUB_UUID）；normal 冷启
    SET active_instance=INSTANCE_NAME（bootstrap，仲裁从首启成立）。
    D6：键经 _key 分 account（account_id=None= A股旧键）。
    """
    import secrets
    uuid_ = secrets.token_hex(8)
    lease_key = _key(LEASE_KEY, account_id)
    gen_key = _key(GEN_KEY, account_id)
    active_key = _key(ACTIVE_INSTANCE_KEY, account_id)
    try:
        got = r.set(lease_key, uuid_, nx=True, ex=30)
    except Exception as e:
        logger.error("租约存储不可达（重试，不退出）: %s", e)
        return False, uuid_, 0
    if not got:
        try:
            holder = r.get(lease_key)
        except Exception:
            holder = "?"
        logger.error("租约被持有（%s），本实例让位退出", holder)
        return False, uuid_, -1   # -1 = 真让位（surrender），0 = 网络问题稍后重试
    try:
        gen = int(r.incr(gen_key))
    except Exception as e:
        logger.error("gen 计数器不可达: %s", e)
        return False, uuid_, 0
    if instance_name:   # M5：normal 冷启补 SET 现任（guarded 已 SET，这里保首启前仲裁不退化）
        try:
            r.set(active_key, instance_name)
        except Exception as e:
            logger.warning("SET active_instance 失败: %s", e)
    return True, uuid_, gen


def _lease_boot(r, instance_name: str = "", account_id=None) -> tuple[str, int]:
    """启动租约获取（先拿权再连行情）：3 次重试；真让位 SystemExit(3)，重试耗尽 os._exit(4)。"""
    for attempt in range(3):
        ok, my_uuid, gen = _lease_acquire(r, instance_name, account_id)
        if ok:
            return my_uuid, gen
        if gen == -1:   # 真让位：写标记退出，unit 的 StartLimit 会接管
            try:
                r.set(_key(SURRENDER_KEY, account_id), datetime.now().isoformat(), ex=600)
            except Exception:
                pass
            raise SystemExit(3)
        time.sleep(5)
    os._exit(4)


_GUARDED_ACQUIRE_LUA = """
local g = tonumber(redis.call('get', KEYS[1]) or '0')
if g == tonumber(ARGV[2]) then
    -- 重启场景：gen 已是 snapshot+1（上次 INCR 过），只抢 lease 不 INCR
    local ok = redis.call('set', KEYS[2], ARGV[1], 'NX', 'EX', 30)
    if not ok then return -2 end
    redis.call('set', KEYS[3], ARGV[3])
    return tonumber(ARGV[2])
elseif g == tonumber(ARGV[2]) - 1 then
    -- 首接场景：gen 还是 snapshot，INCR + 抢 lease + SET 现任（原子绑定）
    local ok = redis.call('set', KEYS[2], ARGV[1], 'NX', 'EX', 30)
    if not ok then return -2 end
    redis.call('incr', KEYS[1])
    redis.call('set', KEYS[3], ARGV[3])
    return tonumber(ARGV[2])
else
    return -1   -- gen 既非 snapshot 也非 snapshot+1（被污染）→ 拒接管，零污染
end
"""


def _lease_acquire_guarded(r, expected_gen: int, target: str, uuid_: str, account_id=None) -> tuple[bool, str, int]:
    """切换目标接管（原子 Lua，M5）。返回 (ok, uuid, gen)。

    gen = expected_gen（首接 INCR 到 snapshot+1 或重启只抢 lease）；<0 失败：
    -1 = gen 污染（被复活 A 抢在 B 前 INCR）拒接管零污染；0 = 存储不可达 / -2 旧 lease 挡。
    """
    try:
        res = int(r.eval(_GUARDED_ACQUIRE_LUA, 3,
                         _key(GEN_KEY, account_id), _key(LEASE_KEY, account_id), _key(ACTIVE_INSTANCE_KEY, account_id),
                         uuid_, expected_gen, target))
    except Exception as e:
        logger.error("guarded 租约存储不可达: %s", e)
        return False, uuid_, 0
    if res < 0:
        return False, uuid_, res   # -1 污染 / -2 旧 lease 挡
    return True, uuid_, res


_CAS_DEL_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def _lease_release(r, uuid_: str, account_id=None) -> bool:
    """A 让位 CAS DEL lease（M5）：lease 值==my_uuid 才删，防删掉 B 已拿到的 lease。"""
    try:
        return bool(int(r.eval(_CAS_DEL_LUA, 1, _key(LEASE_KEY, account_id), uuid_)))
    except Exception as e:
        logger.error("CAS DEL lease 失败: %s", e)
        return False


_LEASE_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


def _write_latest_tick(r, symbol: str, tick, fail_ts: dict, account_id=None) -> None:
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
        r.set(_key(LATEST_TICK_PREFIX.rstrip(":"), account_id) + ":" + symbol, json.dumps({
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


# ThinGateway 批 63 二移驻 src.strategy_framework.md_gateway（层序：strategy_framework
# 层 2 禁 import 层 3；vnpy 网关薄壳归 vnpy 集成层）。
