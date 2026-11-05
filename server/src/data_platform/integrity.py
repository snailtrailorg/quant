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

CRYPTO_EXCHANGES: frozenset[str] = frozenset({"BINANCE", "OKX"})

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


def expected_bars(freq: str, symbol: str, first: date, last: date,
                  open_days: list[date], cal_min: date | None = None,
                  cal_max: date | None = None) -> int:
    """区间 [first, last] 内的预期根数（含两端）。

    open_days: **有序**的开市日列表（trade_cal is_open=1）。
    加密：自然日全算。
    交易日历市场：区间完全落在 trade_cal 覆盖内 → 精确计数；
    部分越界 → 覆盖段精确计数 + 缺口按覆盖段实测开市比率外推（不是拍脑袋 5/7）。
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
        return covered * bpd   # 精确

    # 越界：用覆盖段实测开市比率外推缺口
    cov_lo = max(first, cmin)
    cov_hi = min(last, cmax)
    cov_days = max((cov_hi - cov_lo).days + 1, 0)
    if cov_days <= 0:
        return 0
    ratio = covered / cov_days if cov_days else (5 / 7)
    return max(int(round(covered + (days_total - cov_days) * ratio)), 1) * bpd
