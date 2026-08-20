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
    """详情页聚合主入口。永不抛异常（各块独立降级），坏块为 null/[]。

    副作用（2026-08-20 用户裁定 XTP 为主路径）：upsert 30min 临时订阅——hub ≤30s
    订阅生效后 latest_tick 有值，前端 30s 轮询自动从腾讯快照切 hub 实时；失败不影响详情。
    """
    ts_code, vt = _normalize(symbol)
    _touch_transient_sub(vt)
    return {
        "symbol": vt, "ts_code": ts_code,
        **_slow_block(ts_code),
        "quote": _quote_block(ts_code, vt),
    }


def _touch_transient_sub(vt: str) -> None:
    """看过即订阅：续 30min TTL；上限 100 只挤 expire 最旧（XTP 100 只 tick≈200/s 无压力）。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO hub_transient_subs (symbol, expire_at) "
                "VALUES (%s, now() + interval '30 minutes') "
                "ON CONFLICT (symbol) DO UPDATE SET expire_at = now() + interval '30 minutes'",
                (vt,))
            conn.execute(
                "DELETE FROM hub_transient_subs WHERE symbol IN "
                "(SELECT symbol FROM hub_transient_subs ORDER BY expire_at DESC OFFSET 100)")
            conn.commit()
    except Exception as e:
        logger.debug("临时订阅 upsert 失败（不影响详情）: %s", e)


# ── 分时曲线（17 号蓝图 K 线 Tab"日/分钟"的分钟半边，2026-08-20 补）──

def get_intraday(symbol: str) -> dict | None:
    """当日分时：源 1 bar_hub（hub 自攒分钟，池内订阅标的）；降级源 2 腾讯分时接口。

    返回 {date, source, points: [{t, price, avg, volume}]}，avg=到该时刻 VWAP。
    """
    ts_code, vt = _normalize(symbol)
    r = _intraday_from_hub(vt)
    if r:
        return r
    return _intraday_from_tencent(ts_code)


def _intraday_from_hub(vt: str) -> dict | None:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT ts::date FROM bar_hub WHERE symbol=%s ORDER BY ts DESC LIMIT 1", (vt,))
            row = cur.fetchone()
            if not row:
                return None
            d = row[0]
            cur = conn.execute(
                "SELECT to_char(ts, 'HH24:MI'), close, volume, amount FROM bar_hub "
                "WHERE symbol=%s AND ts::date=%s ORDER BY ts", (vt, d))
            rows = cur.fetchall()
        # 当日点数不足（临时订阅标的当日只从订阅时刻攒起）→ 降级腾讯给全天完整分时；
        # 次日起 hub 自攒全天数据自然接管
        if not rows or len(rows) < 30:
            return None
        points, cum_v, cum_a = [], 0.0, 0.0
        for t, close, volume, amount in rows:
            cum_v += float(volume or 0)
            cum_a += float(amount or 0)
            points.append({"t": t, "price": float(close), "volume": float(volume or 0),
                           "avg": round(cum_a / cum_v, 3) if cum_v > 0 else float(close)})
        return {"date": str(d), "source": "hub", "points": points}
    except Exception as e:
        logger.warning("分时 bar_hub 读取失败: %s", e)
        return None


def _intraday_from_tencent(ts_code: str) -> dict | None:
    """腾讯分时（累计口径差分成分钟量；VWAP=累计额/累计量）。"""
    try:
        import requests
        from .market_snapshot import _tencent_sym
        resp = requests.get(
            f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={_tencent_sym(ts_code)}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        resp.raise_for_status()
        data = (resp.json().get("data") or {}).get(_tencent_sym(ts_code)) or {}
        rows = ((data.get("data") or {}).get("data")) or []
        d = (data.get("data") or {}).get("date") or ""
        if not rows:
            return None
        points = []
        for line in rows:
            p = line.split()
            if len(p) < 3:
                continue
            t, price = p[0], float(p[1])
            cum_v, cum_a = float(p[2]), float(p[3]) if len(p) > 3 else 0.0
            hhmm = f"{t[:2]}:{t[2:]}" if len(t) == 4 else t
            points.append({"t": hhmm, "price": price,
                           "avg": round(cum_a / (cum_v * 100), 4) if cum_v > 0 and cum_a > 0 else price,
                           "cum_v": cum_v})
        # 分钟量 = 累计差分（先取差再删键——上一行的 cum_v 已 pop，用滚动 prev）
        prev_cum = 0.0
        for pt in points:
            cur = pt.pop("cum_v")
            pt["volume"] = max(0.0, cur - prev_cum)
            prev_cum = cur
        return {"date": d, "source": "tencent", "points": points}
    except Exception as e:
        logger.warning("腾讯分时拉取失败 %s: %s", ts_code, e)
        return None


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
            # 清单双表容错：static_symbols（F-DATA-004，周级 beat）可能落后/未同步——
            # fallback asset_static_info（astock_list 日链，2026-08-20 生产实测踩到）
            cur = conn.execute(
                "SELECT name, industry FROM static_symbols WHERE ts_code=%s", (ts_code,))
            row = cur.fetchone()
            if not row:
                # asset_static_info 列 list_status 历史性 NULL，不做该过滤（表本身即上市清单）
                cur = conn.execute(
                    "SELECT name, industry FROM asset_static_info WHERE ts_code=%s", (ts_code,))
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
