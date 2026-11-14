"""数据完整性口径（批170 根因修）：按市场分别计算预期根数。

旧口径（web_api/routes/risk.py 内联）有三处结构性错误，导致「完整性比例长期上不去」：

1. `expected = max(交易日数, 自然日×5/7)`——自然日折工作日 0.714 **恒大于**真实交易日比率
   0.666（一年 243/365），所以 A 股即便一天不缺，pct 也只有 ~93%，永远够不到 complete(≥99%)。
2. 全市场共用 A 股交易日历（trade_cal）——加密 7×24 一年 365 天，用 243 天当分母 ⇒ 实测
   150%~160%，被误判为「超出预期」，而 A 股一侧又被高估。
3. 分钟线同样按自然日 × A 股 240 根/日——加密应为 1440 根/日（1min）、288（5min）。

新口径：市场由交易所后缀判定——BINANCE/OKX=加密（全天候）；SHSE/SZSE/BSE/OF=交易日历。
预期根数 = 区间内该市场的**真实可交易日** × 该频率每日根数。

已知局限（记录在案，不在本批扩面）：停牌日仍计入预期根数——长期停牌股会显示 9x% 而非 100%；
按标的自身上市/停牌日历计算需要标的级事件源，属后续增强。
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date

CRYPTO_EXCHANGES: frozenset[str] = frozenset({"XBIN", "XOKX", "BINANCE", "OKX"})

# 每交易日根数（A 股/场内基金：4 小时连续竞价）
BARS_PER_DAY: dict[str, int] = {"1D": 1, "1min": 240, "5min": 48}
# 加密：24 小时连续
CRYPTO_BARS_PER_DAY: dict[str, int] = {"1D": 1, "1min": 1440, "5min": 288}


def exchange_of(symbol: str) -> str:
    """符号后缀（大小写不敏感）；无后缀返回空串。"""
    return symbol.rsplit(".", 1)[-1].upper() if "." in symbol else ""


def is_crypto(symbol: str) -> bool:
    return exchange_of(symbol) in CRYPTO_EXCHANGES


def bars_per_day(freq: str, symbol: str) -> int:
    return (CRYPTO_BARS_PER_DAY if is_crypto(symbol) else BARS_PER_DAY)[freq]


# ── 批 171b：停牌日历净化口（suspend_calendar 唯一消费口——risk.py/tasks.py/engine 只调口不写 SQL）──


def _suspend_rows(first: date, last: date) -> list[tuple[str, date]]:
    """区间内全日停牌行 [(ts_code, trade_date)]（仅 suspend_type='S' AND full_day='True'——
    R 复牌行当日实有 bar，永不参与净化；full_day 为 text 'True'/'False'，见迁移 0168）。
    表不存在/查询失败返回 []（宁多报不漏报）——**告警留痕**（步4 B-P1-1：裸吞曾把接线
    int 入参错吞成静默全灭，3042 全绿下不可见）。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            return [(r[0], r[1]) for r in conn.execute(
                "SELECT ts_code, trade_date FROM suspend_calendar "
                "WHERE trade_date >= %s AND trade_date <= %s "
                "AND suspend_type = 'S' AND full_day = 'True'", (first, last))]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "suspend_calendar 读取失败（fail-open 宁多报）: %s", e)
        return []


from src.data_platform.schema import vt_to_ts as _vt_to_ts   # 单一真源（含 .SSE 形态，批 137a）


def suspended_on(day: date, vt_symbols: set[str]) -> set[str]:
    """day 当日全日停牌 ∩ vt_symbols（返回 **vt 形态**子集——口内归一，防 ts/vt 错位静默失效）。"""
    hit = {t for t, d in _suspend_rows(day, day)}
    return {v for v in vt_symbols if _vt_to_ts(v) in hit}


def suspend_days_bulk(first: date, last: date, vt_symbols: set[str]) -> dict[str, list[date]]:
    """区间批量净化图：{**vt_symbol**: [窗内全日停牌日]}（同 S+full_day 口径；键 vt 形态——
    与 risk.py 面板循环变量 sym 直接 get，零 N+1：scope 级一次取图内存扣减）。"""
    rows = _suspend_rows(first, last)
    ts_of = {_vt_to_ts(v): v for v in vt_symbols}
    out: dict[str, list[date]] = {}
    for ts, d in rows:
        v = ts_of.get(ts)
        if v is not None:
            out.setdefault(v, []).append(d)
    return out


def delist_map_bulk(categories: tuple[str, ...] = ("stock", "listed_fund", "convertible")
                    ) -> dict[str, date]:
    """批 171b：SM 退市日批量口 {vt_symbol: delist_date}（risk.py 面板消费——查询下沉层 1，
    路由零内联 SQL 铁律）；表不可达返 {}（告警留痕，同 _suspend_rows fail-open 纪律）。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            return {r[0]: r[1] for r in conn.execute(
                "SELECT vt_symbol, delist_date FROM security_master "
                "WHERE category = ANY(%s) AND delist_date IS NOT NULL", (list(categories),))}
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "security_master delist 批量读取失败（fail-open）: %s", e)
        return {}


def expected_bars(freq: str, symbol: str, first: date, last: date,
                  open_days: list[date], cal_min: date | None = None,
                  cal_max: date | None = None,
                  suspend_days: list[date] | None = None) -> int:
    """区间 [first, last] 内的预期根数（含两端）。

    open_days: **有序**的开市日列表（trade_cal is_open=1）。
    加密：自然日全算。
    交易日历市场：区间完全落在 trade_cal 覆盖内 → 精确计数；
    部分越界 → 覆盖段精确计数 + 缺口按覆盖段实测开市比率外推（不是拍脑袋 5/7）。

    批 171b：`suspend_days`（该标的窗内**全日停牌日**列表，suspend_days_bulk 产物）——
    停牌日不计期望（×bpd：1D 减 n、1min 减 n×240）。None=零漂移（调用方未接净化）。
    加密无停牌概念：显式跳过（不消费该参）。外推分支（日历越界段）不减——停牌数据
    新窗才有，老窗如实多报（宁多报不漏报）。
    """
    if last < first:
        return 0
    bpd = bars_per_day(freq, symbol)
    if is_crypto(symbol):
        return ((last - first).days + 1) * bpd

    lo = bisect_left(open_days, first)
    hi = bisect_right(open_days, last)
    covered = hi - lo

    cmin = cal_min or (open_days[0] if open_days else None)
    cmax = cal_max or (open_days[-1] if open_days else None)
    if cmin is None or cmax is None:
        # 日历空：退回粗估（极少见——trade_cal 未同步时）
        return max(int(((last - first).days + 1) * 5 / 7), 1) * bpd

    days_total = (last - first).days + 1
    if first >= cmin and last <= cmax:
        n = covered   # 精确
    else:
        # 越界：用覆盖段实测开市比率外推缺口
        cov_lo = max(first, cmin)
        cov_hi = min(last, cmax)
        cov_days = max((cov_hi - cov_lo).days + 1, 0)
        if cov_days <= 0:
            return 0
        ratio = covered / cov_days if cov_days else (5 / 7)
        n = max(int(round(covered + (days_total - cov_days) * ratio)), 1)
        return n * bpd   # 外推分支不减停牌（数据窗未覆盖，如实多报）
    # 批 171b：全日停牌日不计期望（仅精确分支；crypto 已在上方早退不达此）
    if suspend_days:
        n -= sum(1 for d in suspend_days if first <= d <= last)
    return max(n, 0) * bpd
