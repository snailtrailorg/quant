"""交易时段判定与 staleness 基线工具（2026-08-24 配置化重构）。

从硬编码 A 股时段升级为 market_session 表配置驱动，通过 set_config_provider 回调
避免 quant_common（层 0 底座）直接依赖 data_platform（层 1）。

分层契约：
- quant_common 只存纯逻辑（时段判定、沿检测）+ 注册回调接口
- 上层的 strategy_framework.md_session 注册回调（含 DB 查询）
- 回调返回 market_session 行配置，quant_common 从中取 calendar_dates
  集做交易日判定，不碰 data_platform 的 import
"""
from __future__ import annotations

import datetime as _dt
import time
from typing import Callable

_CONFIG_PROVIDER: Callable | None = None


def set_config_provider(provider: Callable[[str], dict | None]) -> None:
    """注册市场配置查询器（由上层模块启动时调用）。

    Args:
        provider: ``provider(market_name) -> dict | None``
            dict 含 ``calendar``, ``calendar_dates`` (set[date]), ``session_rules``, ``tz``
    """
    global _CONFIG_PROVIDER
    _CONFIG_PROVIDER = provider


def _load_market_config(market: str) -> dict | None:
    """通过回调加载市场配置，缓存 60s。失败返回 None（调用方降级 fallback）。"""
    global _CONFIG_PROVIDER
    if _CONFIG_PROVIDER is None:
        return None
    now = time.time()
    cached = getattr(_load_market_config, "_cache", {}).get(market)
    if cached is not None and now < cached[1]:
        return cached[0]   # 负缓存命中（None）也直接返回——TTL 10s 内不重查
    try:
        cfg = _CONFIG_PROVIDER(market)
        cache = getattr(_load_market_config, "_cache", {})
        if cfg:
            cache[market] = (cfg, now + 60.0)
            _load_market_config._cache = cache
            return cfg
        # 负缓存（盲审 B-P1：miss 只在真值时写缓存→on_tick 热路径每 tick 一发 SELECT）——
        # 短 TTL 10s 收敛快（DB 恢复/行新建后 10s 内可达），chatter 消除
        cache[market] = (None, now + 10.0)
        _load_market_config._cache = cache
    except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
        pass
    return None


def _is_trading_day(cfg: dict, d: _dt.date) -> bool:
    """基于配置的交易日判定。

    calendar_dates 由 provider 从 data_platform 解析，quant_common 不碰 DB。
    """
    cal = cfg.get("calendar", "weekday")
    if cal == "always":
        return True
    if cal == "never":
        return False
    if cal == "weekday" and d.weekday() >= 5:
        return False
    cdates = cfg.get("calendar_dates")
    if cdates is not None:
        return d in cdates
    return d.weekday() < 5  # fallback


# 别名→market_session 表行名（盲审 A/B 同判 P1 修：归一只做查询容错，方向=表名）。
# market_session 种子行名（迁移 0053）='A股'/'加密永续'——注册表名 astock/crypto 查表前须经此映射。
_TABLE_NAME_ALIASES = {"astock": "A股", "crypto": "加密永续"}


def _norm_market(market: str) -> str:
    """注册表名归一（'A股'/'加密'→'astock'/'crypto'——骨架/EXCHANGES 键形态；表查询另走 _TABLE_NAME_ALIASES）。"""
    return {"A股": "astock", "加密": "crypto"}.get(market, market)


def in_session(market: str = "A股", now: _dt.datetime | None = None) -> bool:
    """判断某市场当前是否在交易时段。

    Args:
        market: 市场名称（market_session.name），默认 A股
        now: 指定时刻（默认当前时间）

    配置缺失时降级到旧版硬编码（A 股 9:31-11:30/13:01-15:00 + weekday）。
    """
    now = now or _dt.datetime.now()
    # 盲审 A/B 同判 P1 修：DB 查询用**原键**（表行名='A股'）——归一改写查询键会让 market_session
    # 表整体变死配置（0053 运营真源+节假日日历守卫全失）。注册表名（astock/crypto）miss 后经
    # _TABLE_NAME_ALIASES 容错重查；骨架 fallback 才用 _norm_market。
    cfg = _load_market_config(market)
    if cfg is None:
        tbl = _TABLE_NAME_ALIASES.get(_norm_market(market))
        if tbl and tbl != market:
            cfg = _load_market_config(tbl)
    if cfg is None:
        # 无配置：fallback 到 MarketSpec 骨架（批 66c 收编——原 A 股硬编码单源化；
        # crypto 无配置时错落 A 股时段的坑同修：骨架 "24x7" 恒 True）
        from .markets import MARKETS
        skel = MARKETS.get(_norm_market(market), {}).get("sessions_skeleton")
        if skel == "24x7":
            return True
        if now.weekday() >= 5:
            return False
        hm = now.hour * 100 + now.minute
        if not skel:
            return (931 <= hm <= 1130) or (1301 <= hm <= 1500)
        for op, cl in skel:
            try:
                o, c = int(op.replace(":", "")), int(cl.replace(":", ""))
            except ValueError:
                continue
            if o <= hm <= c:
                return True
        return False

    # 交易日判定
    if not _is_trading_day(cfg, now.date()):
        return False

    # 时段规则判定
    hm = now.hour * 100 + now.minute
    rules = cfg.get("session_rules", [])
    for r in rules:
        try:
            op = int(r["open"].replace(":", ""))
            cl = int(r["close"].replace(":", ""))
        except (KeyError, ValueError):
            continue
        # 跨夜规则（如 21:00-02:30）：close < open 时跨夜
        if cl < op:
            if hm >= op or hm < cl:
                return True
        elif op <= hm <= cl:
            return True
    return False


def in_astock_session(now=None) -> bool:
    """（兼容）A 股交易时段。等价于 in_session('A股')，旧代码零改动。"""
    return in_session("A股", now)


def session_edge(cur: bool, was: bool) -> bool:
    """交易时段进入沿（False->True）。staleness 基线在沿上清零：跨日/午休/竞价窗口
    都不继承旧基线（S6 修订；三处循环共用，勿内联各写一份）。"""
    return cur and not was
