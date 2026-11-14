"""批 172c P2a · 声明式规格层（既有表映射，不新建规格表）。

来源：
- MarketSpec ← market_hours（运营真源）+ MARKETS 骨架（tz/channels）
- InstrumentSpec ← security_master + band_rules（symbol 覆盖 > exchange×board > 代码缺省）
- AccountPermSpec ← trading_account + account_permission（可缓存）
- AccountSnapshot ← account_snapshot（每单现读，禁进配置缓存）

缓存：key=(kind,key,cfg:version)；版本变化自然失效；TTL 由调用方/版本轮询兜底。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.data_platform.db import get_conn
from src.quant_common.markets import ASSET_CATEGORIES, MARKETS

logger = logging.getLogger(__name__)

_SESSION_OF_MARKET = {"astock": "astock_main", "crypto": "crypto_247"}
_CACHE: dict[tuple, object] = {}
# 上次成功的规格（DB 抖动时的 stale 兜底；键 (kind,key)，与版本无关）
_LAST_GOOD: dict[tuple[str, str], object] = {}
_STALE: set[tuple[str, str]] = set()


def _cfg_version() -> int:
    try:
        from src.data_platform.routing import CFG_VERSION_KEY
        from src.quant_common.redis_client import business_redis
        r = business_redis(decode_responses=True)
        v = r.get(CFG_VERSION_KEY)
        return int(v) if v and str(v).isdigit() else 0
    except Exception:
        return 0


def _cached(kind: str, key: str, fn):
    """版本缓存 + 上次成功兜底（读失败 → stale 快照 + 告警，交 RuleSet 按 mode 分流）。"""
    ver = _cfg_version()
    k = (kind, key, ver)
    if k not in _CACHE:
        # 只保留当前版本（防版本刷新增内存）
        for old in [x for x in _CACHE if x[0] == kind]:
            if old[2] != ver:
                _CACHE.pop(old, None)
        try:
            _CACHE[k] = fn()
            _LAST_GOOD[(kind, key)] = _CACHE[k]
            _STALE.discard((kind, key))
        except Exception as e:
            last = _LAST_GOOD.get((kind, key))
            if last is None:
                raise
            _STALE.add((kind, key))
            _CACHE[k] = last
            logger.warning("规格读取失败，沿用上次快照（stale）%s/%s: %s", kind, key, e)
    return _CACHE[k]


def is_stale(kind: str, key: str) -> bool:
    return (kind, key) in _STALE


@dataclass(frozen=True)
class MarketSpec:
    market: str
    tz: str
    calendar: str
    anchor: str
    sessions: list[dict]
    channels: list[str]

    @classmethod
    def of(cls, market: str, phase: str | None = None) -> "MarketSpec":
        return _cached("market", market, lambda: _load_market(market))


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    market: str
    exchange: str
    category: str
    board: str | None
    tick_size: Decimal
    multiplier: Decimal
    lot_size: int
    t_plus: int
    price_band: dict | None
    sellable_rule: str
    order_types: list[str]
    position_side: str
    order_range: dict | None
    venue_flags: dict
    mic_code: str | None = None     # 批175e：ISO 10383 venue 标识（crypto NULL；MPID 语义非证券主键）

    @classmethod
    def of(cls, symbol: str) -> "InstrumentSpec":
        return _cached("instrument", symbol, lambda: _load_instrument(symbol))


@dataclass(frozen=True)
class AccountPermSpec:
    account_id: int
    market: str
    allowed_categories: set[str]
    allowed_exchanges: set[str]
    allowed_boards: set[str]
    st_allowed: bool
    convertible_allowed: bool
    data_license: str
    close_policy: dict       # {opponent_depth, aggression, max_order_qty}

    @classmethod
    def of(cls, account_id: int) -> "AccountPermSpec":
        return _cached("perm", str(account_id), lambda: _load_perm(account_id))


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: int
    ts: datetime | None
    available_cash: float
    total_value: float
    margin_used: float
    equity: float

    @classmethod
    def of(cls, account_id: int) -> "AccountSnapshot":
        return _load_snapshot(account_id)   # 每单现读，禁止缓存


def _load_market(market: str) -> MarketSpec:
    sk = MARKETS.get(market) or {}
    session_id = _SESSION_OF_MARKET.get(market, "")
    tz = sk.get("timezone") or "+08:00"
    calendar = "trade_cal" if market == "astock" else "none"
    anchor = "15:00" if market == "astock" else "00:00"
    sessions: list[dict] = []
    try:
        with get_conn() as c:
            row = c.execute(
                "SELECT tz, calendar, anchor, sessions FROM market_hours WHERE session_id=%s",
                (session_id,)).fetchone()
        if row:
            tz, calendar, anchor = row[0], row[1], row[2]
            if hasattr(anchor, "strftime"):
                anchor = anchor.strftime("%H:%M")
            else:
                anchor = str(anchor or "")
            sessions = row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]")
    except Exception:
        pass
    channels = list((sk.get("channels") or {}).keys())
    return MarketSpec(market, tz, calendar, anchor, sessions, channels)


def _parse_t_plus(trade_phase: str | None) -> int:
    txt = (trade_phase or "T+1").strip().upper()
    if txt.startswith("T+"):
        try:
            return int(txt[2:])
        except ValueError:
            return 1
    return 1


def _load_instrument(symbol: str) -> InstrumentSpec:
    with get_conn() as c:
        row = c.execute(
            "SELECT vt_symbol, market, exchange, category, board, multiplier, tick_size, "
            "trade_phase, sellable_rule, order_types, position_side, venue_flags, "
            "order_range_override, mic_code FROM security_master WHERE vt_symbol=%s", (symbol,)).fetchone()
        if not row:
            raise KeyError(f"InstrumentSpec: security_master 无行 {symbol}")
        (_sym, _market, exch, cat, board, mult, tick, phase, sellable,
         order_types, pos_side, flags, override, mic) = row
        band = None
        if board:
            band = c.execute(
                "SELECT pct_normal, pct_st, has_limit_band, order_range_mode, "
                "order_range_lower_pct, order_range_upper_pct FROM band_rules "
                "WHERE exchange=%s AND board=%s", (exch, board)).fetchone()

    price_band = None
    order_range = None
    if band:
        price_band = {"normal_pct": float(band[0]), "st_pct": float(band[1]),
                      "has_limit_band": bool(band[2])}
        mode = band[3] or "none"
        if mode != "none":
            order_range = {"mode": mode,
                           "lower_pct": float(band[4]) if band[4] is not None else None,
                           "upper_pct": float(band[5]) if band[5] is not None else None}
    if override:
        src = override if isinstance(override, dict) else json.loads(override)
        if src:
            order_range = dict(src)

    if cat == "perp":
        pos_side = "both" if (pos_side or "long_only") == "long_only" else pos_side
        lot = 1
        t_plus = 0
    else:
        lot = int(mult or 1)
        t_plus = _parse_t_plus(phase)

    return InstrumentSpec(
        symbol=row[0], market=_market, exchange=exch, category=cat, board=board,
        tick_size=Decimal(str(tick)), multiplier=Decimal(str(mult or 1)),
        lot_size=lot, t_plus=t_plus, price_band=price_band,
        sellable_rule=sellable or "volume_minus_frozen",
        order_types=list(order_types or []),
        position_side=pos_side or "long_only",
        order_range=order_range,
        venue_flags=(flags if isinstance(flags, dict) else json.loads(flags or "{}")),
        mic_code=mic,
    )


def _load_perm(account_id: int) -> AccountPermSpec:
    with get_conn() as c:
        acc = c.execute(
            "SELECT market, params FROM trading_account WHERE id=%s", (account_id,)).fetchone()
        if not acc:
            raise KeyError(f"AccountPermSpec: trading_account 无行 {account_id}")
        perm = c.execute(
            "SELECT allowed_categories, allowed_exchanges, allowed_boards, "
            "is_st_allowed, convertible_allowed FROM account_permission WHERE account_id=%s",
            (account_id,)).fetchone()
    params = acc[1] or {}
    if isinstance(params, str):
        params = json.loads(params)
    if not perm:
        # 无权限行=零权限（perms.py 同款 fail-closed），不是不限
        return AccountPermSpec(account_id, acc[0], set(), set(), set(), False, False,
                               str(params.get("data_license") or ""),
                               dict(params.get("close_policy") or {}))
    cats = {("listed_fund" if c == "etf" else c) for c in (perm[0] or [])}
    return AccountPermSpec(
        account_id=account_id, market=acc[0],
        allowed_categories=cats, allowed_exchanges=set(perm[1] or []),
        allowed_boards=set(perm[2] or []), st_allowed=bool(perm[3]),
        convertible_allowed=bool(perm[4]),
        data_license=str(params.get("data_license") or ""),
        close_policy=dict(params.get("close_policy") or {}),
    )


def _load_snapshot(account_id: int) -> AccountSnapshot:
    try:
        with get_conn() as c:
            row = c.execute(
                "SELECT ts, available_cash, total_value, margin_used, equity "
                "FROM account_snapshot WHERE account_id=%s ORDER BY ts DESC LIMIT 1",
                (account_id,)).fetchone()
    except Exception:
        row = None
    if not row:
        return AccountSnapshot(account_id, None, 0.0, 0.0, 0.0, 0.0)
    ts, cash, total, margin_used, equity = row
    return AccountSnapshot(
        account_id=account_id, ts=ts, available_cash=float(cash or 0),
        total_value=float(total or 0), margin_used=float(margin_used or 0),
        equity=float(equity if equity is not None else (total or 0)),
    )


def invalidate() -> None:
    """测试/管理端写后清缓存（生产靠 cfg:version 自然失效）。"""
    _CACHE.clear()
    _STALE.clear()


# ── 规格管理写侧（路由只调这里；新路由禁内联 SQL）────────────────

_INSTRUMENT_FIELDS = {
    "sellable_rule": "text", "order_types": "jsonb", "position_side": "text",
    "venue_flags": "jsonb", "order_range_override": "jsonb",
}
_BAND_FIELDS = {
    "order_range_mode": "text", "order_range_lower_pct": "numeric",
    "order_range_upper_pct": "numeric", "has_limit_band": "bool",
}


def get_instrument_row(symbol: str) -> dict | None:
    with get_conn() as c:
        row = c.execute(
            "SELECT vt_symbol, market, exchange, category, board, multiplier, tick_size, "
            "trade_phase, sellable_rule, order_types, position_side, venue_flags, "
            "order_range_override, effective_from, source, version "
            "FROM security_master WHERE vt_symbol=%s", (symbol,)).fetchone()
    if not row:
        return None
    keys = ["symbol", "market", "exchange", "category", "board", "multiplier", "tick_size",
            "trade_phase", "sellable_rule", "order_types", "position_side", "venue_flags",
            "order_range_override", "effective_from", "source", "version"]
    return dict(zip(keys, row))


def update_instrument(symbol: str, fields: dict) -> dict:
    bad = set(fields) - set(_INSTRUMENT_FIELDS)
    if bad:
        raise ValueError(f"不可编辑字段: {sorted(bad)}")
    if not fields:
        return get_instrument_row(symbol) or {}
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f"{k}=%s")
        vals.append(json.dumps(v, ensure_ascii=False) if _INSTRUMENT_FIELDS[k] == "jsonb" else v)
    vals.append(symbol)
    with get_conn() as c:
        c.execute(f"UPDATE security_master SET {', '.join(sets)}, version=version+1, "
                  f"source='admin', updated_at=now() WHERE vt_symbol=%s", vals)
        c.commit()
    invalidate()
    return get_instrument_row(symbol) or {}


def get_band_rule(exchange: str, board: str) -> dict | None:
    with get_conn() as c:
        row = c.execute(
            "SELECT exchange, board, pct_normal, pct_st, has_limit_band, order_range_mode, "
            "order_range_lower_pct, order_range_upper_pct FROM band_rules "
            "WHERE exchange=%s AND board=%s", (exchange, board)).fetchone()
    if not row:
        return None
    return dict(zip(["exchange", "board", "pct_normal", "pct_st", "has_limit_band",
                     "order_range_mode", "order_range_lower_pct", "order_range_upper_pct"], row))


def update_band_rule(exchange: str, board: str, fields: dict) -> dict:
    bad = set(fields) - set(_BAND_FIELDS)
    if bad:
        raise ValueError(f"不可编辑字段: {sorted(bad)}")
    if not fields:
        return get_band_rule(exchange, board) or {}
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f"{k}=%s")
        vals.append(v)
    vals.extend([exchange, board])
    with get_conn() as c:
        c.execute(f"UPDATE band_rules SET {', '.join(sets)} WHERE exchange=%s AND board=%s", vals)
        c.commit()
    invalidate()
    return get_band_rule(exchange, board) or {}


def _get_params(c, account_id: int) -> dict:
    row = c.execute("SELECT params FROM trading_account WHERE id=%s", (account_id,)).fetchone()
    if not row:
        raise KeyError(f"trading_account 无行 {account_id}")
    params = row[0] or {}
    return json.loads(params) if isinstance(params, str) else dict(params)


def get_account_close_policy(account_id: int) -> dict | None:
    """账户级平仓策略（params.close_policy）；无行返回 None（不许抛）。"""
    try:
        with get_conn() as c:
            params = _get_params(c, account_id)
        return dict(params.get("close_policy") or {}) or None
    except Exception:
        return None


def get_close_policy(account_id: int) -> dict:
    with get_conn() as c:
        params = _get_params(c, account_id)
    return dict((params.get("close_policy") or {}))


def update_close_policy(account_id: int, policy: dict) -> dict:
    depth = int(policy.get("opponent_depth", 1) or 1)
    aggression = float(policy.get("aggression", 0.10) or 0.10)
    max_qty = int(policy.get("max_order_qty", 100000) or 100000)
    if not 1 <= depth <= 5:
        raise ValueError("opponent_depth 需在 1..5")
    if not 0 <= aggression <= 0.5:
        raise ValueError("aggression 需在 0..0.5")
    if max_qty < 1:
        raise ValueError("max_order_qty 需 >=1")
    with get_conn() as c:
        params = _get_params(c, account_id)
        params["close_policy"] = {"opponent_depth": depth, "aggression": aggression,
                                  "max_order_qty": max_qty}
        c.execute("UPDATE trading_account SET params=%s::jsonb, updated_at=now() WHERE id=%s",
                  (json.dumps(params, ensure_ascii=False), account_id))
        c.commit()
    invalidate()
    return get_close_policy(account_id)


def update_impl(account_id: int, impl: str) -> str:
    """双轨开关（legacy/plugin）；只改账号行，任务下次启动生效。"""
    value = str(impl or "").strip().lower()
    if value not in ("legacy", "plugin"):
        raise ValueError("impl 只能是 legacy 或 plugin")
    with get_conn() as c:
        row = c.execute("SELECT 1 FROM trading_account WHERE id=%s", (account_id,)).fetchone()
        if not row:
            raise KeyError(f"trading_account 无行 {account_id}")
        c.execute("UPDATE trading_account SET impl=%s, updated_at=now() WHERE id=%s",
                  (value, account_id))
        c.commit()
    invalidate()
    return value


def get_data_license(account_id: int) -> str:
    with get_conn() as c:
        return str(_get_params(c, account_id).get("data_license") or "")


def update_data_license(account_id: int, value: str) -> str:
    with get_conn() as c:
        params = _get_params(c, account_id)
        params["data_license"] = str(value or "")
        c.execute("UPDATE trading_account SET params=%s::jsonb, updated_at=now() WHERE id=%s",
                  (json.dumps(params, ensure_ascii=False), account_id))
        c.commit()
    invalidate()
    return get_data_license(account_id)
