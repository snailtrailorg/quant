"""批 56a·M1：SecurityMaster/MarketHours（29 号 §四契约）。

SMClient：标的属性查询（主档+时变）+covers（M2 resolve 硬过滤预留）+engine 专用写侧
（upsert_rows/upsert_state——填充链唯一写通道，web 侧零写）。
MarketHours：节奏域查询（时段/日锚/竞价判定）——28 §4.2 立法的运行期切片；
HTTP 面用 get_market_hours() 单例（进程内缓存方有效）。

盲审修订（2026-09-20 双盲 A/B）：写侧 executemany 化（18 号 §2.1）+全列 upsert
（品类值+生命周期列——新标的不落 server_default 错值）；covers 单查批量化；
is_auction scope 板块大小写归一+交易日历感知；sessions 无行不缓存（负缓存清除）；
day_anchor 表驱动（market_hours.anchor）返 datetime（29 号契约签名）。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

logger = logging.getLogger("data_platform.security_master")

# YYYYMMDD / YYYY-MM-DD（engine 填充链日期列清洗用——脏值统一 NULL 不炸批）
_DATE_RE = re.compile(r"^\d{4}-?\d{2}-?\d{2}$")


def _null_date(v):
    """日期列脏值清洗：合法字符串原样/None 透传/date 对象 ISO 化/其余（空串/'None'/畸形）→ None。"""
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str) and _DATE_RE.match(v):
        return v
    return None


@dataclass(frozen=True)
class SecurityAttr:
    """security_master 一行的形状（SMClient.get 返回）。"""
    vt_symbol: str
    market: str
    exchange: str
    category: str
    name: str | None
    industry: str | None
    multiplier: float
    tick_size: float
    session_id: str
    trade_phase: str
    routing_hints: dict


@dataclass(frozen=True)
class Phase:
    """market_hours.sessions 的一档（28 §4.2——scope 语法 交易所[:板块]）。"""
    phase: str
    start: str
    end: str
    scope: str | None = None     # None=该 session 全市场；"SZSE|BSE|SHSE:STAR" 形态
    cancel: bool | None = None   # 竞价撤单语义（pre 可撤/auction 不可撤）


class SMClient:
    """security_master/security_state 读侧 + engine 填充链写侧。"""

    def get(self, vt_symbol: str) -> SecurityAttr | None:
        """主档一行；无此标的返回 None（消费方自行决定 DataGap 语义）。"""
        from .db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT vt_symbol, market, exchange, category, name, industry, multiplier, "
                "tick_size, session_id, trade_phase, routing_hints "
                "FROM security_master WHERE vt_symbol=%s", (vt_symbol,))
            r = cur.fetchone()
        if not r:
            return None
        hints = r[10] or {}
        if isinstance(hints, str):
            try:
                hints = json.loads(hints)
            except (TypeError, ValueError):
                hints = {}
        return SecurityAttr(r[0], r[1], r[2], r[3], r[4], r[5],
                            float(r[6]), float(r[7]), r[8], r[9], hints)

    def effective_attr(self, vt_symbol: str, kind: str, at: date) -> dict | None:
        """时变查询：effective_from<=at 的最新一行（st/limit_band/conv_price/margin_tier）。"""
        from .db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT value FROM security_state "
                "WHERE vt_symbol=%s AND kind=%s AND effective_from<=%s "
                "ORDER BY effective_from DESC LIMIT 1", (vt_symbol, kind, at))
            r = cur.fetchone()
        if not r:
            return None
        v = r[0]
        return json.loads(v) if isinstance(v, str) else v

    def covers(self, scope, symbols: tuple[str, ...]) -> bool:
        """范围覆盖判定（M2 resolve 硬过滤预留——batch 57 接线）。

        库内有档：按 (market,exchange,category) 逐项过 scope.covers_one；
        库内无档：以 vt_symbol 交易所后缀近似（无档标的不因此一票否决——SM 未回填≠不可交易）。
        单查批量取档（ANY），逐 symbol get 为 N+1（盲审 B）。
        """
        if not symbols:
            return True
        from .db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT vt_symbol, market, exchange, category FROM security_master "
                "WHERE vt_symbol = ANY(%s)", (list(symbols),))
            known = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
        for s in symbols:
            tri = known.get(s)
            if tri is not None:
                if not scope.covers_one(*tri):
                    return False
            else:
                exch = s.rsplit(".", 1)[1] if "." in s else ""
                if scope.exchanges is not None and exch and exch not in scope.exchanges:
                    return False
                # 无档近似补 market 维度（盲审 A：BTCUSDT.BINANCE 不再被 astock 行
                # exchanges=NULL 通配放行——后缀→市场映射）
                mkt = self._market_of_suffix(exch)
                if mkt and scope.markets and mkt not in scope.markets:
                    return False
        return True

    @staticmethod
    def _market_of_suffix(exch: str) -> str | None:
        """交易所后缀→市场（无档近似维度；未知后缀 None=不否决——SM 未回填≠不可用）。"""
        if exch in ("SHSE", "SZSE", "BSE"):
            return "astock"
        if exch in ("BINANCE", "OKX"):
            return "crypto"
        return None

    def upsert_rows(self, rows: list[tuple]) -> int:
        """填充链写侧（engine 同步任务调用——executemany 单批，18 号 §2.1）。

        rows 元组序=(vt_symbol, market, exchange, category, name, industry,
        multiplier, tick_size, trade_phase, list_date, delist_date)。
        品类值随行携带（新标的 INSERT 不落 server_default 错值——转债 T+0/乘数 10 等）；
        DO UPDATE 字段级更新（29 §四：append-only 会让 delist_date/名称变更永远为空）；
        list_date 空值不抹旧（COALESCE），delist_date 恒覆盖（退市事实只进不退）。
        """
        clean = [(*r[:9], _null_date(r[9]), _null_date(r[10])) for r in rows]
        if not clean:
            return 0
        from .db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO security_master (vt_symbol, market, exchange, category, name, "
                    "industry, multiplier, tick_size, trade_phase, list_date, delist_date) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (vt_symbol) DO UPDATE SET "
                    "name=EXCLUDED.name, industry=EXCLUDED.industry, "
                    "multiplier=EXCLUDED.multiplier, tick_size=EXCLUDED.tick_size, "
                    "trade_phase=EXCLUDED.trade_phase, "
                    "list_date=COALESCE(EXCLUDED.list_date, security_master.list_date), "
                    "delist_date=EXCLUDED.delist_date, updated_at=now()", clean)
            conn.commit()
        return len(clean)

    def upsert_state(self, rows: list[tuple]) -> int:
        """时变行写侧（rows=(vt_symbol, effective_from, kind, value_json_str)——幂等 executemany）。"""
        if not rows:
            return 0
        from .db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO security_state (vt_symbol, effective_from, kind, value) "
                    "VALUES (%s,%s,%s,%s::jsonb) "
                    "ON CONFLICT (vt_symbol, effective_from, kind) DO UPDATE SET value=EXCLUDED.value",
                    list(rows))
            conn.commit()
        return len(rows)


def _tz_of(s: str):
    """tz 列解析：偏移式（'+08:00'）→ timezone；命名式（'UTC'/'Asia/Shanghai'）→ ZoneInfo。"""
    if s and s[0] in "+-":
        sign = -1 if s[0] == "-" else 1
        return timezone(sign * timedelta(hours=int(s[1:3]), minutes=int(s[4:6])))
    from zoneinfo import ZoneInfo
    return ZoneInfo(s)


class MarketHours:
    """market_hours/band_rules 读侧（28 §4.2 节奏域）。HTTP 面经 get_market_hours() 单例复用。"""

    def __init__(self):
        self._cache: dict[str, list[Phase]] = {}
        self._anchor_cache: dict[str, tuple[time, str]] = {}

    def sessions(self, session_id: str, at: date) -> list[Phase]:
        """时段序列（低频数据进程内缓存；scope 板块过滤由消费方按标的交易所套用）。

        无此 session_id 不缓存（行后补可见——负缓存会钉死空表，盲审 A）。
        """
        if session_id not in self._cache:
            from .db import get_conn
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT sessions FROM market_hours WHERE session_id=%s", (session_id,))
                r = cur.fetchone()
            if r is None:
                return []                       # 无行不缓存
            raw = r[0] if r else []
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (TypeError, ValueError):
                    raw = []
            self._cache[session_id] = [
                Phase(p.get("phase", ""), p.get("start", ""), p.get("end", ""),
                      p.get("scope"), p.get("cancel"))
                for p in raw
            ]
        return self._cache[session_id]

    def day_anchor(self, session_id: str) -> datetime:
        """日界锚（29 号契约 ->datetime；28 §3.2——astock 15:00(+08:00) / crypto UTC 00:00）。

        表驱动（market_hours.anchor+tz 列）；返回"今天"在该市场时区的锚点 aware datetime。
        历史锚点由消费方按日平移（签名无 at——契约变更走 29 号流程）。
        """
        if session_id not in self._anchor_cache:
            from .db import get_conn
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT anchor, tz FROM market_hours WHERE session_id=%s", (session_id,))
                r = cur.fetchone()
            if r is None:
                return datetime.combine(date.today(), time(0, 0))
            self._anchor_cache[session_id] = (r[0], r[1])
        anchor, tz = self._anchor_cache[session_id]
        return datetime.combine(date.today(), anchor, tzinfo=_tz_of(tz))

    @staticmethod
    def _board_of(vt_symbol: str) -> str:
        """板块判定（scope 语法 交易所[:板块] 的消费侧——27 号'board 暂不建实体'由代码前缀承载）。

        STAR=688/689；CHINEXT=300/301；BSE=92/43/83/87；其余=main。
        """
        code = vt_symbol.split(".", 1)[0]
        if code.startswith(("688", "689")):
            return "star"
        if code.startswith(("300", "301")):
            return "chinext"
        if code.startswith(("92", "43", "83", "87")):
            return "bse"
        return "main"

    @staticmethod
    def _scope_match(scope: str, exch: str, board: str) -> bool:
        """scope 语法 交易所[:板块]（28 §4.2）："SHSE:STAR"=沪市仅科创板——板块例外须双匹配。

        板块段大小写归一（种子大写 STAR vs _board_of 小写 star——盲审 B 实测错配）。
        is_auction 判定与 applicable_phases 展示同源（共一 helper，防两处口径漂移）。
        """
        for tok in scope.split("|"):
            parts_ = tok.split(":")
            if parts_[0] == exch and (len(parts_) == 1 or parts_[1].lower() == board):
                return True
        return False

    def applicable_phases(self, vt_symbol: str, session_id: str = "astock_main") -> list[Phase]:
        """该标的适用的时段序列（scope 板块过滤——展示面与 is_auction 判定同源）。

        沪主板不显示 close_auct/post_fix（无收盘竞价/无盘后固定价——仅科创创业适用）。
        """
        exch = vt_symbol.rsplit(".", 1)[1] if "." in vt_symbol else ""
        board = self._board_of(vt_symbol)
        return [p for p in self.sessions(session_id, date.today())
                if p.scope is None or self._scope_match(p.scope, exch, board)]

    def is_auction(self, vt_symbol: str, at: datetime) -> bool:
        """竞价阶段判定（含 scope 板块过滤：close_auct 深市/北交/沪**仅科创**）。

        日历感知：astock_main 经 trade_cal 判交易日（非交易日恒 False——盲审 A）；
        calendar='none' 的 session（crypto）跳过日历。日历未覆盖年 fail-open（同 XTP 连接窗立法）。
        """
        from .db import get_conn, is_trading_day
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT calendar FROM market_hours WHERE session_id='astock_main'")
            r = cur.fetchone()
        if r and r[0] and r[0] != "none" and not is_trading_day(at.date()):
            return False
        exch = vt_symbol.rsplit(".", 1)[1] if "." in vt_symbol else ""
        board = self._board_of(vt_symbol)
        for p in self.sessions("astock_main", at.date()):
            if p.phase not in ("pre", "auction", "close_auct"):
                continue
            hh, mm = int(p.start[:2]), int(p.start[3:5])
            eh, em = int(p.end[:2]), int(p.end[3:5])
            t = at.hour * 60 + at.minute
            if hh * 60 + mm <= t < eh * 60 + em:
                if p.scope is None or self._scope_match(p.scope, exch, board):
                    return True
        return False

    def band_of(self, exchange: str, board: str, is_st: bool) -> float | None:
        """涨跌停幅度（29 v3 六审——limit_band 规则派生：st 状态×板块规则表）。"""
        from .db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT {} FROM band_rules WHERE exchange=%s AND board=%s".format(
                    "pct_st" if is_st else "pct_normal"), (exchange, board))
            r = cur.fetchone()
        return float(r[0]) if r and r[0] is not None else None


_hours: MarketHours | None = None


def get_market_hours() -> MarketHours:
    """MarketHours 单例（HTTP 常驻进程复用进程内缓存——每请求 new 则缓存无效，盲审 A）。"""
    global _hours
    if _hours is None:
        _hours = MarketHours()
    return _hours
