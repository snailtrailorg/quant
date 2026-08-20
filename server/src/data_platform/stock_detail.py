"""标的详情聚合（三档第三档项 14+17）：三源合一 + 按需拉取 + Valkey 缓存。

层位裁定（17 号 §2）：聚合逻辑在数据平台层，web_api 只做薄壳端点。

数据源与降级链：
- quote（实时）：hub latest_tick（池内订阅标的，秒级）→ 腾讯单股（非池，60s TTL）→ null
- 一档表（全市场）：stk_limit / moneyflow / top_list / block_trade / namechange 直读
- 二档表（池内）：cyq_chips / income / fina_indicator 直读；非池按需 Tushare（5min TTL）
- 缓存（项 17）：慢变块（除 quote 外全部）Valkey 10min——财务/事件数据日级更新，
  防详情页高频刷新打 DB；quote 不缓存（各源自带 TTL）。
"""
from __future__ import annotations
import json
import logging
import os

logger = logging.getLogger("data_platform.stock_detail")

SLOW_KEY_PREFIX = "detail:slow:"
SLOW_TTL = 600
ONDEMAND_KEY_PREFIX = "detail:ondemand:"
ONDEMAND_TTL = 300


def _r():
    """模块级单例（O 审 M7）。"""
    global _R
    import redis
    if _R is None:
        _R = redis.Redis.from_url(
            os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True, socket_timeout=5)
    return _R


_R = None


def _normalize(symbol: str) -> tuple[str, str]:
    """任意格式 → (ts_code, vt_symbol)。"""
    from src.data_platform.schema import to_vt_symbol, vt_to_ts
    s = symbol.strip()
    vt = to_vt_symbol(s)
    return vt_to_ts(vt), vt


def get_stock_detail(symbol: str) -> dict:
    """详情页聚合主入口。永不抛异常（各块独立降级），坏块为 null/[]。"""
    ts_code, vt = _normalize(symbol)
    return {
        "symbol": vt, "ts_code": ts_code,
        **_slow_block(ts_code),
        "quote": _quote_block(ts_code, vt),
    }


# ── analyze 缓存封装（B10：web_api 不 import 私有 _r）──

def analyze_cache_get(ts_code: str) -> str | None:
    try:
        return _r().get(f"detail:analyze:{ts_code}")
    except Exception:
        return None


def analyze_cache_set(ts_code: str, text: str, ttl: int = 600) -> None:
    try:
        _r().set(f"detail:analyze:{ts_code}", text, ex=ttl)
    except Exception:
        pass


# ── 实时块（不缓存，各源自带 TTL）──

def _quote_block(ts_code: str, vt: str) -> dict | None:
    try:
        raw = _r().get("hub:latest_tick:" + vt)
        if raw:
            q = json.loads(raw)
            q["source"] = "hub"
            return q
    except Exception as e:
        logger.warning("hub tick 读失败 %s: %s", vt, e)
    from .market_snapshot import get_quote
    return get_quote(ts_code)


# ── 慢变块（Valkey 10min）──

def _slow_block(ts_code: str) -> dict:
    key = SLOW_KEY_PREFIX + ts_code
    try:
        cached = _r().get(key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.warning("详情缓存读失败: %s", e)
    block = _build_slow(ts_code)
    # 只缓存完整块：DB 抖动导致的部分降级不落缓存（否则缺块 10min）
    if _CACHEABLE_KEYS <= block.keys():
        try:
            _r().set(key, json.dumps(block, ensure_ascii=False), ex=SLOW_TTL)
        except Exception as e:
            logger.warning("详情缓存写失败（不阻断）: %s", e)
    return block


_CACHEABLE_KEYS = {"name", "in_pool", "limit", "moneyflow", "events",
                   "name_changes", "chips", "finance"}


def _build_slow(ts_code: str) -> dict:
    from src.data_platform.db import get_conn
    from src.data_platform.schema import to_vt_symbol
    vt = to_vt_symbol(ts_code)
    block: dict = {}
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT name, industry FROM static_symbols WHERE ts_code=%s", (ts_code,))
            row = cur.fetchone()
            block["name"], block["industry"] = (row[0], row[1]) if row else (None, None)
            cur = conn.execute(
                "SELECT COUNT(*) FROM pool_symbols ps JOIN pools p ON p.id=ps.pool_id "
                "WHERE ps.symbol=%s AND p.category='astock'", (vt,))
            block["in_pool"] = (cur.fetchone()[0] or 0) > 0
            cur = conn.execute(
                "SELECT trade_date, pre_close, up_limit, down_limit FROM stk_limit "
                "WHERE ts_code=%s ORDER BY trade_date DESC LIMIT 1", (ts_code,))
            row = cur.fetchone()
            block["limit"] = ({"trade_date": row[0], "pre_close": _ff(row[1]),
                               "up_limit": _ff(row[2]), "down_limit": _ff(row[3])}
                              if row else None)
            cur = conn.execute(
                "SELECT trade_date, buy_lg_amount, sell_lg_amount, net_mf_amount FROM moneyflow "
                "WHERE ts_code=%s ORDER BY trade_date DESC LIMIT 5", (ts_code,))
            block["moneyflow"] = [
                {"trade_date": r[0], "buy_lg": float(r[1] or 0), "sell_lg": float(r[2] or 0),
                 "net_mf": float(r[3] or 0)} for r in cur.fetchall()]
            block["events"] = _events(conn, ts_code)
            block["name_changes"] = _name_changes(conn, ts_code)
    except Exception as e:
        logger.warning("详情慢变块构建失败（部分降级）: %s", e)
    block["chips"] = _chips(ts_code, block.get("in_pool", False))
    block["finance"] = _finance(ts_code, block.get("in_pool", False))
    return block


def _events(conn, ts_code: str) -> list[dict]:
    """龙虎榜/大宗/解禁/质押合并时间线（按日期倒序取 20）。"""
    events: list[dict] = []
    try:
        cur = conn.execute(
            "SELECT trade_date, reason, close, net_amount FROM top_list WHERE ts_code=%s "
            "ORDER BY trade_date DESC LIMIT 10", (ts_code,))
        events += [{"type": "top_list", "date": r[0], "detail": r[1],
                    "close": float(r[2]) if r[2] else None, "net_amount": float(r[3]) if r[3] else None}
                   for r in cur.fetchall()]
        cur = conn.execute(
            "SELECT trade_date, price, vol, amount, buyer, seller FROM block_trade WHERE ts_code=%s "
            "ORDER BY trade_date DESC LIMIT 10", (ts_code,))
        events += [{"type": "block_trade", "date": r[0], "price": float(r[1]) if r[1] else None,
                    "vol": float(r[2]) if r[2] else None, "amount": float(r[3]) if r[3] else None,
                    "buyer": r[4], "seller": r[5]} for r in cur.fetchall()]
        cur = conn.execute(
            "SELECT float_date, float_share, float_ratio, holder_name FROM share_float WHERE ts_code=%s "
            "ORDER BY float_date DESC LIMIT 10", (ts_code,))
        events += [{"type": "share_float", "date": r[0], "float_share": float(r[1]) if r[1] else None,
                    "float_ratio": float(r[2]) if r[2] else None, "holder": r[3]} for r in cur.fetchall()]
        cur = conn.execute(
            "SELECT end_date, pledge_count, pledge_ratio FROM pledge_stat WHERE ts_code=%s "
            "ORDER BY end_date DESC LIMIT 3", (ts_code,))
        events += [{"type": "pledge", "date": r[0], "pledge_count": _ff(r[1]),
                    "pledge_ratio": _ff(r[2])} for r in cur.fetchall()]
    except Exception as e:
        logger.warning("事件时间线构建失败: %s", e)
    events.sort(key=lambda x: x["date"] or "", reverse=True)
    return events[:20]


def _name_changes(conn, ts_code: str) -> list[dict]:
    try:
        cur = conn.execute(
            "SELECT name, start_date, end_date, change_reason FROM namechange "
            "WHERE ts_code=%s ORDER BY start_date DESC LIMIT 5", (ts_code,))
        return [{"name": r[0], "start": r[1], "end": r[2], "reason": r[3]} for r in cur.fetchall()]
    except Exception as e:
        logger.warning("namechange 构建失败: %s", e)
        return []


# ── 池内外分叉块 ──

def _chips(ts_code: str, in_pool: bool) -> dict | None:
    """筹码分布：池内直读 cyq_chips 最新日；非池按需 Tushare（5min TTL）。"""
    if in_pool:
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT trade_date FROM cyq_chips WHERE ts_code=%s "
                    "ORDER BY trade_date DESC LIMIT 1", (ts_code,))
                row = cur.fetchone()
                if not row:
                    return None
                cur = conn.execute(
                    "SELECT price, percent FROM cyq_chips WHERE ts_code=%s AND trade_date=%s "
                    "ORDER BY price", (ts_code, row[0]))
                rows = [(float(r[0]), float(r[1])) for r in cur.fetchall()]
                return {"trade_date": row[0], "source": "db", "dist": rows} if rows else None
        except Exception as e:
            logger.warning("chips 池内读取失败: %s", e)
            return None
    return _ondemand(ts_code, "chips", _pull_chips)


def _finance(ts_code: str, in_pool: bool) -> dict | None:
    """财务摘要：池内直读最新一期；非池按需 Tushare。"""
    if in_pool:
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT end_date, ann_date, total_revenue, n_income, basic_eps, rd_exp "
                    "FROM income WHERE ts_code=%s ORDER BY ann_date DESC, end_date DESC LIMIT 1",
                    (ts_code,))
                inc = cur.fetchone()
                cur = conn.execute(
                    "SELECT end_date, roe, roa, gross_margin, netprofit_margin, debt_to_assets, "
                    "revenue_yoy, netprofit_yoy FROM fina_indicator WHERE ts_code=%s "
                    "ORDER BY ann_date DESC, end_date DESC LIMIT 1", (ts_code,))
                fina = cur.fetchone()
                if not inc and not fina:
                    return None
                return {
                    "income": {"end_date": inc[0], "ann_date": inc[1],
                               "total_revenue": _ff(inc[2]), "n_income": _ff(inc[3]),
                               "basic_eps": _ff(inc[4]), "rd_exp": _ff(inc[5])} if inc else None,
                    "indicator": {"end_date": fina[0], "roe": _ff(fina[1]), "roa": _ff(fina[2]),
                                  "gross_margin": _ff(fina[3]), "netprofit_margin": _ff(fina[4]),
                                  "debt_to_assets": _ff(fina[5]), "revenue_yoy": _ff(fina[6]),
                                  "netprofit_yoy": _ff(fina[7])} if fina else None,
                    "source": "db",
                }
        except Exception as e:
            logger.warning("finance 池内读取失败: %s", e)
            return None
    return _ondemand(ts_code, "finance", _pull_finance)


def _ff(v):
    """None/NaN/不可数值化 → None（O 审 S2：pandas 缺值 NaN 直通 JSON 序列化 500）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f   # NaN 自反不等


def _ondemand(ts_code: str, kind: str, pull_fn) -> dict | None:
    """非池按需拉取 + 5min TTL 缓存（三档心智：打开才拉不落库）。"""
    key = f"{ONDEMAND_KEY_PREFIX}{kind}:{ts_code}"
    try:
        cached = _r().get(key)
        if cached:
            return json.loads(cached) if cached != "null" else None
    except Exception:
        pass
    try:
        result = pull_fn(ts_code)
    except Exception as e:
        logger.warning("按需拉取失败 %s/%s: %s", ts_code, kind, e)
        return None
    try:
        _r().set(key, json.dumps(result, ensure_ascii=False) if result else "null", ex=ONDEMAND_TTL)
    except Exception:
        pass
    return result


def _pull_chips(ts_code: str) -> dict | None:
    from src.data_platform.adapters import tushare_adapter
    # 不传 trade_date：cyq_chips 盘后才有当日数，返回近 60 日——取最新日档位（2026-08-20 实测
    # 单标的 6000 行/99 档；盘中传当日恒空）
    df = tushare_adapter.get_pro().cyq_chips(ts_code=ts_code)
    if df is None or df.empty:
        return None
    latest = df["trade_date"].max()
    sub = df[df["trade_date"] == latest]
    rows = sorted(((float(r.price), float(r.percent)) for r in sub.itertuples()), key=lambda x: x[0])
    return {"trade_date": str(latest), "source": "tushare", "dist": rows}


def _pull_finance(ts_code: str) -> dict | None:
    import pandas as pd
    from src.data_platform.adapters import tushare_adapter
    pro = tushare_adapter.get_pro()
    inc_df = pro.income(ts_code=ts_code, report_type="1")
    fina_df = pro.fina_indicator(ts_code=ts_code)
    _s = lambda v: str(v) if v is not None and pd.notna(v) else None
    income = None
    if inc_df is not None and not inc_df.empty:
        row = inc_df.sort_values(["ann_date", "end_date"], ascending=False).iloc[0]
        income = {"end_date": _s(row.get("end_date")), "ann_date": _s(row.get("ann_date")),
                  "total_revenue": _ff(row.get("total_revenue")), "n_income": _ff(row.get("n_income")),
                  "basic_eps": _ff(row.get("basic_eps")), "rd_exp": _ff(row.get("rd_exp"))}
    indicator = None
    if fina_df is not None and not fina_df.empty:
        row = fina_df.sort_values(["ann_date", "end_date"], ascending=False).iloc[0]
        indicator = {"end_date": _s(row.get("end_date")),
                     "roe": _ff(row.get("roe")), "roa": _ff(row.get("roa")),
                     "gross_margin": _ff(row.get("gross_margin")),
                     "netprofit_margin": _ff(row.get("netprofit_margin")),
                     "debt_to_assets": _ff(row.get("debt_to_assets")),
                     "revenue_yoy": _ff(row.get("revenue_yoy")),
                     "netprofit_yoy": _ff(row.get("netprofit_yoy"))}
    if not income and not indicator:
        return None
    return {"income": income, "indicator": indicator, "source": "tushare"}
