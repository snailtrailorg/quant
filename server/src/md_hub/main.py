"""共享行情 Hub（ST7，设计 docs/architecture/14 v2）。

单进程持 XTP MD 连续 + 全市场合约，tick→MinuteAggregator→Valkey Streams 分发。
纯数据面：无下单/无风控（R-HALT1）；零 TD 会话。

启动: python -m src.md_hub.main
systemd: quant-md-hub@quant（单元在 server/scripts/systemd/）

关键机制（对齐需求书 R-*）：
- 租约+gen（R-DL4）：SET NX EX 30 + Lua CAS 续期；gen=INCR hub:gen 永不回退（评审 F1）
- 分钟桶=分钟末标注（R-BR9 口径对齐 Tushare）；volume/amount 累计差分（评审 S3）
- 11:30:05/15:00:05 双 flush（评审 S2）；untrusted 双门限（R-BR4）
- bar 落 bar_hub 表（影子期，R-CAP3/F2）；心跳/看门狗/tick 断流自杀（R-AV1/S6）
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
    from vnpy.event import EventEngine
except ImportError:
    EventEngine = None

from src.strategy_runner.main import _guard, _sd_notify, _alert, _in_astock_session  # SA 机制复用

BAR_STREAM_PREFIX = "hub:bars:"
LEASE_KEY = "hub:lease"
GEN_KEY = "hub:gen"
HB_KEY = "quant:hb:md-hub"
SURRENDER_KEY = "hub:surrender"
STREAM_MAXLEN = 5000          # ≈20 交易日分钟 bar（评审：慢消费者 3 周不读才可能被剪）
PG_FLUSH_INTERVAL = 10.0      # bar 落库批量间隔（独立线程，R-BR7 不反压分发）
PG_QUEUE_MAX = 5000           # 落库缓冲上限（溢出丢最旧+告警，有界保证）


def _valkey():
    import redis
    return redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
                                decode_responses=True, socket_timeout=3)


def _project_symbol(tick) -> str:
    """vnpy vt 后缀 → 项目后缀（SSE→SHSE），bar 表/流命名统一项目口径。"""
    ex = tick.exchange.value if tick.exchange else ""
    return f"{tick.symbol}.{'SHSE' if ex == 'SSE' else ex}"


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

    def flush_all(self) -> list[dict]:
        """定时 flush（11:30:05/15:00:05，评审 S2）：finalize 全部在桶。"""
        bars = []
        for symbol in list(self._buckets.keys()):
            b = self._buckets.pop(symbol, None)
            if b:
                bars.append(self._finalize(symbol, b))
        return bars

    def _finalize(self, symbol: str, b: dict) -> dict:
        from datetime import timedelta
        prev = self._last_acc.get(symbol)
        volume = max(0.0, b["vol_acc"] - (prev[0] if prev else 0.0))
        amount = max(0.0, b["amt_acc"] - (prev[1] if prev else 0.0))
        self._last_acc[symbol] = (b["vol_acc"], b["amt_acc"])
        span = (b["last_tick"] - b["first_tick"]).total_seconds()
        # 评审 S6：收盘/午休末桶（11:29/14:59 起）按构造稀疏——豁免双门限，防每日误冻结
        closing = b["minute"].hour * 60 + b["minute"].minute in (11 * 60 + 29, 14 * 60 + 59)
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


_LEASE_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if EventEngine is None:
        logger.error("vnpy 未安装")
        raise SystemExit(1)

    r = _valkey()

    # ——— 租约/代次（先拿权再连行情）———
    my_uuid, gen = "", 0
    for attempt in range(3):
        ok, my_uuid, gen = _lease_acquire(r)
        if ok:
            break
        if gen == -1:   # 真让位：写标记退出，unit 的 StartLimit 会接管
            try:
                r.set(SURRENDER_KEY, datetime.now().isoformat(), ex=600)
            except Exception:
                pass
            raise SystemExit(3)
        time.sleep(5)
    else:
        os._exit(4)
    logger.info("hub 启动：uuid=%s gen=%d", my_uuid, gen)

    # ——— 行情接入（ThinGateway + MdApi，零 TD）———
    from vnpy.trader.gateway import BaseGateway
    from vnpy_xtp.gateway.xtp_gateway import XtpMdApi
    from src.strategy_runner.main import _build_xtp_setting

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

    ee = EventEngine()
    ee.start()   # 绕开 MainEngine 必须自启（构造不启动，_active=False → 线程未活）
    gw = ThinGateway(ee, "XTP")
    md_api = XtpMdApi(gw)
    gw.md_api = md_api

    agg = MinuteAggregator()
    seqs: dict[str, int] = {}
    seqs_lock = threading.Lock()   # 评审 B4：事件线程 on_tick 与主循环 flush 并发 _publish
    # ticks/bars=进程累计（观测）；sess_*=时段内基线（S6 修订：进入交易时段的沿上清零，
    # 跨日/午休/竞价窗口都不继承旧基线——昨夜回放 tick 不再污染今晨断流判定）
    stats = {"ticks": 0, "bars": 0, "last_tick_wall": 0.0,
             "sess_ticks": 0, "sess_last_tick": 0.0}
    pgw = _PGWriter()
    pgw.start()

    @_guard("hub.on_tick")
    def on_tick(event):
        tick = event.data
        symbol = _project_symbol(tick)
        stats["ticks"] += 1
        stats["last_tick_wall"] = time.time()
        if _in_astock_session():
            stats["sess_ticks"] += 1
            stats["sess_last_tick"] = stats["last_tick_wall"]
        bar = agg.on_tick(symbol, tick)
        if bar:
            _publish(bar)

    def _publish(bar: dict) -> None:
        with seqs_lock:
            try:
                r.xadd(BAR_STREAM_PREFIX + bar["symbol"], msg_of(bar, seqs.get(bar["symbol"], 0) + 1),
                       maxlen=STREAM_MAXLEN, approximate=True)
                seqs[bar["symbol"]] = seqs.get(bar["symbol"], 0) + 1   # 评审 B1：成功后才占号（失败不留洞）
            except Exception as e:
                logger.error("XADD 失败（bar 丢失，告警）: %s", e)
                _alert("hub XADD 失败（bar 丢失）", f"{bar['symbol']} {bar['ts']}")
                return
        stats["bars"] += 1
        pgw.push(bar)

    def msg_of(bar: dict, seq: int) -> dict:
        return {
            "gen": gen, "seq": seq,
            "ts": bar["ts"].isoformat(), "pub_ts": time.time(),
            "untrusted": int(bar.get("untrusted", False)),
            "open": bar["open"], "high": bar["high"], "low": bar["low"], "close": bar["close"],
            "volume": bar["volume"], "amount": bar["amount"], "tick_count": bar["tick_count"],
        }

    from vnpy.event import EventEngine as _EE
    from vnpy.trader.event import EVENT_TICK
    ee.register(EVENT_TICK, on_tick)

    # ——— 连接 + 订阅（真相源=DB，30s diff + 60s 幂等重放，R-SUB）———
    setting = _build_xtp_setting()
    md_api.connect(setting["账号"], setting["密码"], int(setting["客户号"]),
                   setting["行情地址"], int(setting["行情端口"]), setting.get("行情协议", "TCP"), 3)

    from vnpy.trader.object import SubscribeRequest
    from vnpy.trader.constant import Exchange
    _EX = {"SHSE": Exchange.SSE, "SZSE": Exchange.SZSE, "BSE": getattr(Exchange, "BSE", Exchange.SSE)}
    subscribed: set[str] = set()
    md_status_was = False

    def _desired_symbols() -> set[str]:
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:   # 影子查询必须在 with 内——曾因缩进在块外用到已还池连接被静默吞（2026-08-17 实测 subs=0）
                cur = conn.execute(
                    "SELECT DISTINCT symbol FROM live_task WHERE status='running' AND symbol IS NOT NULL")
                rows = {x[0] for x in cur.fetchall() if x[0]}
                try:
                    cur = conn.execute("SELECT value FROM system_config WHERE key='hub_shadow_symbols'")
                    row = cur.fetchone()
                    if row and row[0]:
                        rows |= {s.strip() for s in row[0].split(",") if s.strip()}
                except Exception as e:
                    logger.warning("读 hub_shadow_symbols 失败: %s", e)
                return rows
        except Exception as e:
            logger.warning("读订阅真相源失败（沿用旧集）: %s", e)
            return subscribed

    def _subscribe(sym: str) -> None:
        try:
            raw, ex = sym.rsplit(".", 1)
            e = _EX.get(ex)
            if e:
                md_api.subscribe(SubscribeRequest(symbol=raw, exchange=e))
        except Exception as e:
            logger.warning("订阅失败 %s: %s", sym, e)

    def _sync_subscriptions(force: bool = False) -> None:
        nonlocal subscribed
        want = _desired_symbols()
        # 评审 C3：除 diff 外，每 60s 无条件全量幂等重放（XTP 重连不恢复订阅 + 启动竞态双兜底）
        replay_all = force or (int(time.time()) % 60 < 10)
        if replay_all or want != subscribed:
            logger.info("订阅同步：%s（共 %d）", sorted(want), len(want))
            for s in want:
                _subscribe(s)
            subscribed = want

    _sync_subscriptions(force=True)
    logger.info("hub 就绪，初始订阅 %d", len(subscribed))

    # ——— 主循环 ———
    counter = 0
    sess_was = _in_astock_session()   # 时段沿检测（S6 修订：沿上清 sess_* 基线）
    flush_points = {1130, 1500}   # 11:30:05 / 15:00:05 双 flush（评审 S2）
    try:
        while True:
            time.sleep(10)
            counter += 1
            _sd_notify("WATCHDOG=1")
            # 租约续期（Lua CAS，评审）：失败=让位退出（另一 hub 在位或存储异常区分告警）
            try:
                renewed = r.eval(_LEASE_RENEW_LUA, 1, LEASE_KEY, my_uuid, "30")
                if not int(renewed):
                    logger.critical("租约续期失败（被抢占或丢失），退出")
                    _alert("行情 hub 租约丢失，实例退出", "另一实例在位或存储异常；systemd 将接管。")
                    os._exit(5)
            except SystemExit:
                raise
            except Exception as e:
                logger.error("租约续期异常（容忍一轮）: %s", e)
            # 事件线程存活（R-BR12）
            t = getattr(ee, "_thread", None)
            if t is not None and not t.is_alive():
                logger.critical("hub 事件线程死亡，退出待重启")
                _alert("行情 hub 事件线程死亡，自动重启", "")
                os._exit(1)
            # 订阅同步（30s）+ 断线重连沿重订阅（SA2 hub 版）
            md_status = bool(getattr(md_api, "connect_status", True))
            if md_status and not md_status_was:
                _sync_subscriptions(force=True)
                logger.info("MD 重连沿：重放全部订阅")
            md_status_was = md_status
            if counter % 3 == 0:
                _sync_subscriptions()
            # 双 flush（分钟末后 5s）
            now = datetime.now()
            hm = now.hour * 100 + now.minute
            if hm in flush_points and 5 <= now.second < 10:   # 评审 S7：避开 :00-:04 仍在进 tick 的窗口
                for bar in agg.flush_all():
                    _publish(bar)
            # 心跳（R-OBS1）
            try:
                r.hset(HB_KEY, mapping={
                    "pid": os.getpid(), "gen": gen, "subs": len(subscribed),
                    "ticks": stats["ticks"], "bars": stats["bars"],
                    "sess_ticks": stats["sess_ticks"],
                    "last_tick_ts": stats["last_tick_wall"] or 0, "dropped_pg": pgw.dropped,
                })
                r.expire(HB_KEY, 90)
            except Exception as e:
                logger.warning("心跳写失败: %s", e)
            # tick 断流检测（S6 修订 2026-08-18：只告警不自杀——重启是 liveness 工具不是疗法，
            # 数据缺席的原因在平台/网络/交易所规则，重启一概治不了；进程级故障由 watchdog/事件线程检查兜）
            # 基线=本时段内首 tick（进入沿清零）：跨日回放 tick/假日/竞价静默窗口都不再误判
            sess_now = _in_astock_session()
            if sess_now and not sess_was:
                stats["sess_ticks"] = 0
                stats["sess_last_tick"] = 0.0
            sess_was = sess_now
            if sess_now and stats["sess_ticks"] == 0 and counter % 30 == 0:
                _alert("hub 交易时段零 tick（订阅可能未生效/XTP 异常）",
                       f"订阅 {len(subscribed)} 个标的。runbook：journalctl -u quant-md-hub@quant；"
                       f"确认 MD 连接后可手动 systemctl restart quant-md-hub@quant。")
            if sess_now and stats["sess_ticks"] > 0 and stats["sess_last_tick"]:
                _stale = time.time() - stats["sess_last_tick"]
                if _stale > 300 and counter % 6 == 0:   # 每 60s 一条，避免轰炸
                    logger.critical("hub tick 断流 %.0fs（时段内已收 %d 条，只告警不自杀）",
                                    _stale, stats["sess_ticks"])
                    _alert(f"行情 hub tick 断流 {_stale:.0f}s",
                           f"时段内已收 {stats['sess_ticks']} 条后断流。runbook：查 journalctl -u quant-md-hub@quant；"
                           f"确认为行情源/网络问题后可手动 systemctl restart quant-md-hub@quant（worker 会自动暖机补缺）。")
    except KeyboardInterrupt:
        pass
    finally:
        os._exit(0)   # 原生库拆除规避（同 runner）；SystemExit 路径已直接 os._exit 带码


if __name__ == "__main__":
    main()
