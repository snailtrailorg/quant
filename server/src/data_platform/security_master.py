"""批 56a·M1：SecurityMaster/MarketHours 读接口（29 号 §四契约）。

SMClient：标的属性查询（主档+时变）+covers（M2 resolve 硬过滤预留）。
MarketHours：节奏域查询（时段/日锚/竞价判定）——28 §4.2 立法的运行期切片。
边界：只读——属性数据来自同步填充链（engine 侧 upsert），本模块零写。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time

logger = logging.getLogger("data_platform.security_master")


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
    """security_master/security_state 读侧。"""

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
        """
        for s in symbols:
            a = self.get(s)
            if a is not None:
                if not scope.covers_one(a.market, a.exchange, a.category):
                    return False
            else:
                exch = s.rsplit(".", 1)[1] if "." in s else ""
                if scope.exchanges is not None and exch and exch not in scope.exchanges:
                    return False
        return True

    def upsert_rows(self, rows: list[tuple]) -> int:
        """填充链写侧（engine 同步任务调用——ON CONFLICT DO UPDATE 字段级更新，
        29 号 §四：append-only 会让 delist_date/名称变更永远为空）。

        rows 元组序=(vt_symbol, market, exchange, category, name, industry)。
        """
        from .db import get_conn
        n = 0
        with get_conn() as conn:
            for vt, mkt, exch, cat, name, industry in rows:
                conn.execute(
                    "INSERT INTO security_master (vt_symbol, market, exchange, category, name, industry) "
                    "VALUES (%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (vt_symbol) DO UPDATE SET "
                    "name=EXCLUDED.name, industry=EXCLUDED.industry, updated_at=now()",
                    (vt, mkt, exch, cat, name, industry))
                n += 1
            conn.commit()
        return n


    def upsert_state(self, rows: list[tuple]) -> int:
        """时变行写侧（rows=(vt_symbol, effective_from, kind, value_json_str)——幂等）。"""
        from .db import get_conn
        n = 0
        with get_conn() as conn:
            for vt, eff, kind, value in rows:
                conn.execute(
                    "INSERT INTO security_state (vt_symbol, effective_from, kind, value) "
                    "VALUES (%s,%s,%s,%s::jsonb) "
                    "ON CONFLICT (vt_symbol, effective_from, kind) DO UPDATE SET value=EXCLUDED.value",
                    (vt, eff, kind, value))
                n += 1
            conn.commit()
        return n


class MarketHours:
    """market_hours/band_rules 读侧（28 §4.2 节奏域）。"""

    def __init__(self):
        self._cache: dict[str, list[Phase]] = {}

    def sessions(self, session_id: str, at: date) -> list[Phase]:
        """时段序列（低频数据进程内缓存；scope 板块过滤由消费方按标的交易所套用）。"""
        if session_id not in self._cache:
            from .db import get_conn
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT sessions FROM market_hours WHERE session_id=%s", (session_id,))
                r = cur.fetchone()
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

    def day_anchor(self, session_id: str) -> time:
        """日界锚（28 §3.2——astock 15:00 / crypto 00:00）。"""
        return time(15, 0) if session_id == "astock_main" else time(0, 0)

    @staticmethod
    def _board_of(vt_symbol: str) -> str:
        """板块判定（scope 语法 交易所[:板块] 的消费侧——27 号'board 暂不建实体'由代码前缀承载）。

        STAR=688/689；CHINEXT=300/301；BSE=92/43/83；其余=main。
        """
        code = vt_symbol.split(".", 1)[0]
        if code.startswith(("688", "689")):
            return "star"
        if code.startswith(("300", "301")):
            return "chinext"
        if code.startswith(("92", "43", "83", "87")):
            return "bse"
        return "main"

    def is_auction(self, vt_symbol: str, at: datetime) -> bool:
        """竞价阶段判定（含 scope 板块过滤：close_auct 深市/北交/沪**仅科创**）。"""
        exch = vt_symbol.rsplit(".", 1)[1] if "." in vt_symbol else ""
        board = self._board_of(vt_symbol)
        for p in self.sessions("astock_main", at.date()):
            if p.phase not in ("pre", "auction", "close_auct"):
                continue
            hh, mm = int(p.start[:2]), int(p.start[3:5])
            eh, em = int(p.end[:2]), int(p.end[3:5])
            t = at.hour * 60 + at.minute
            if hh * 60 + mm <= t < eh * 60 + em:
                if p.scope is None:
                    return True
                # scope 语法 交易所[:板块]（28 §4.2）："SHSE:STAR"=沪市仅科创板——板块例外须双匹配
                for tok in p.scope.split("|"):
                    parts_ = tok.split(":")
                    if parts_[0] == exch and (len(parts_) == 1 or parts_[1] == board):
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
