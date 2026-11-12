"""批 172c P2b · 规则引擎（RuleSet：五维规则的唯一判定源）。

规则来源全部来自声明式规格（specs.py）：MarketSpec / InstrumentSpec / AccountPermSpec。
本模块**不做 provider 分支**，也不直读 system_config；定价族由 adapters 规则面委托到此。

失败语义三分（D32 §7.4）：
- 规格缺失（missing）→ 开仓拒，平仓走 close_fallback_price 四级兜底；
- 规格非法（invalid）→ 开仓拒，平仓兜底 + 告警；
- 读失败（stale）→ 有上次快照用快照 + 告警，无快照按缺失。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

logger = logging.getLogger(__name__)

# provider 缺省策略（仅当账户 params 无 close_policy 时使用）
PROVIDER_CLOSE_DEFAULTS: dict[str, dict] = {
    "xtp": {"opponent_depth": 1, "aggression": 0.10, "max_order_qty": 100000},
    "emt": {"opponent_depth": 1, "aggression": 0.10, "max_order_qty": 100000},
    "crypto": {"opponent_depth": 1, "aggression": 0.10, "max_order_qty": 100000},
}
_DEFAULT_CLOSE = {"opponent_depth": 1, "aggression": 0.10, "max_order_qty": 100000}


class RuleViolation(Exception):
    """规则拒绝（携带稳定错误码供 API/日志消费）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# category → 动态规则（停牌/临停等品种特例）；启动时校验完整性
_DYNAMIC_RULES: dict[str, Callable[[Any, Any], list[str]]] = {}
REQUIRED_DYNAMIC_CATEGORIES = ("convertible",)


def register_dynamic_rule(category: str, fn: Callable[[Any, Any], list[str]]) -> None:
    _DYNAMIC_RULES[category] = fn


def registered_categories() -> set[str]:
    return set(_DYNAMIC_RULES)


def convertible_halt_rule(intent: Any, spec: Any) -> list[str]:
    """可转债临停（沪 20%/30%、深 20%/30% 临停规则）占位：由 venue_flags 声明。"""
    flags = getattr(spec, "venue_flags", None) or {}
    if flags.get("halted"):
        return ["SUSPENDED"]
    return []


register_dynamic_rule("convertible", convertible_halt_rule)


def close_fallback_price(tick: dict | None, action: str, close_policy: dict | None,
                         has_limit_band: bool = True) -> float | None:
    """平仓四级兜底：跌停线 → 对手价×aggression → 最新价钳带内 → None（转人工）。"""
    tick = tick or {}
    pol = close_policy or _DEFAULT_CLOSE
    side = "bid" if str(action).upper() == "SELL" else "ask"
    if has_limit_band:
        try:
            lower = float(tick.get("lower_limit") or 0)
        except (TypeError, ValueError):
            lower = 0.0
        if lower > 0:
            return lower
    # 对手价 × 让价
    levels = tick.get(side) or []
    depth = int(pol.get("opponent_depth", 1) or 1)
    opp = 0.0
    if 1 <= depth <= len(levels):
        try:
            opp = float(levels[depth - 1] or 0)
        except (TypeError, ValueError):
            opp = 0.0
    aggression = float(pol.get("aggression", 0.10) or 0.10)
    if opp > 0:
        factor = 1 - aggression if str(action).upper() == "SELL" else 1 + aggression
        return opp * factor
    # 最新价（不乘让价），钳到涨跌停带内
    try:
        last = float(tick.get("last_price") or tick.get("close") or 0)
    except (TypeError, ValueError):
        last = 0.0
    if last <= 0:
        return None
    try:
        upper = float(tick.get("upper_limit") or 0)
    except (TypeError, ValueError):
        upper = 0.0
    if has_limit_band and lower > 0 and last < lower:
        return lower
    if has_limit_band and upper > 0 and last > upper:
        return upper
    return last


@dataclass(frozen=True)
class RuleSet:
    ms: Any            # MarketSpec
    ins: Any           # InstrumentSpec
    acc: Any           # AccountPermSpec | None
    state: str = "fresh"   # fresh | stale | missing

    # ── 账户策略 ────────────────────────────────────────
    @property
    def close_policy(self) -> dict:
        pol = getattr(self.acc, "close_policy", None) or {}
        return {**_DEFAULT_CLOSE, **{k: v for k, v in pol.items() if v is not None}}

    @property
    def has_limit_band(self) -> bool:
        band = self.ins.price_band or {}
        return bool(band.get("has_limit_band", True))

    # ── 方向 / 可卖 / 上限 ──────────────────────────────
    def close_action(self, pos: Any) -> str:
        return "BUY" if getattr(pos, "direction", "") == "direction_short" else "SELL"

    def sellable(self, pos: Any) -> int:
        vol = int(getattr(pos, "volume", 0) or 0)
        if self.ins.sellable_rule == "yd_volume":
            return max(int(getattr(pos, "yd_volume", 0) or 0), 0)
        frozen = int(getattr(pos, "frozen", 0) or 0)
        return max(vol - frozen, 0)

    def max_close_qty(self) -> int:
        try:
            return int(self.close_policy.get("max_order_qty", 100000) or 100000)
        except (TypeError, ValueError):
            return 100000

    def round_price(self, px: Any) -> float:
        try:
            px = float(px)
        except (TypeError, ValueError):
            return 0.0
        if px <= 0:
            return 0.0
        tick = float(self.ins.tick_size or 0)
        if tick > 0:
            return round(round(px / tick) * tick, 6)
        return round(px, 3)

    # ── 定价 ────────────────────────────────────────────
    def reference_price(self, tick: dict | None, pos: Any, action: str) -> float:
        px = close_fallback_price(tick, action, self.close_policy, self.has_limit_band)
        return float(px) if px and px > 0 else 0.0

    # ── 合规 ────────────────────────────────────────────
    def pre_trade_checks(self, intent: Any, tick: dict | None) -> None:
        """停牌 / 临停 / 行情新鲜度。停牌拒开仓，平仓放行（减风险方向）。"""
        mode = "reduce" if _is_reduce(intent) else "open"
        sym = getattr(intent, "symbol", "") or ""
        if sym:
            try:
                from datetime import date

                from src.data_platform.integrity import suspended_on
                hits = suspended_on(date.today(), {sym})
                if hits and mode == "open":
                    raise RuleViolation("SUSPENDED", f"标的停牌，禁止开仓: {sym}")
                if hits:
                    logger.warning("停牌标的减仓放行（交易所将拒）: %s", sym)
            except RuleViolation:
                raise
            except Exception as e:
                logger.warning("停牌检查失败（按未停牌继续）: %s", e)
        for fn in _DYNAMIC_RULES.values():
            try:
                codes = fn(intent, self.ins)
            except Exception as e:
                logger.warning("动态规则执行失败: %s", e)
                continue
            if codes and mode == "open":
                raise RuleViolation(codes[0], f"动态规则拒绝: {codes}")
        last = (tick or {}).get("last_price")
        if last is None:
            logger.warning("规则检查缺行情快照（按 bar close 参考）: %s", sym)

    def validate(self, intent: Any, tick: dict | None, mode: str = "open") -> None:
        """合规校验：缺规则 fail-closed（开仓）/ 兜底（平仓）。"""
        if self.state == "stale":
            logger.warning("规则快照过期（沿用上次成功值）: %s", getattr(intent, "symbol", ""))
        if self.state == "missing":
            if mode == "open":
                raise RuleViolation("RULE_MISSING", f"规则未配置: {getattr(intent, 'symbol', '')}")
            logger.warning("平仓规则缺失，走四级兜底: %s", getattr(intent, "symbol", ""))
            return
        self.pre_trade_checks(intent, tick)
        if mode == "reduce":
            return
        # 数量按最小交易单位
        lot = int(self.ins.lot_size or 1)
        vol = float(getattr(intent, "volume", 0) or 0)
        if lot > 0 and vol > 0 and abs(vol / lot - round(vol / lot)) > 1e-9:
            raise RuleViolation("LOT_SIZE", f"委托量 {vol} 非最小单位 {lot} 的整数倍")
        # 委托类型
        if self.ins.order_types:
            ot = str(getattr(intent, "order_type", "") or "").lower()
            if ot and ot not in [str(x).lower() for x in self.ins.order_types]:
                raise RuleViolation("ORDER_TYPE", f"不支持委托类型 {ot}")
        # 申报价在涨跌停带内
        if self.has_limit_band:
            px = float(getattr(intent, "price", 0) or 0)
            band = self.ins.price_band or {}
            pct = float(band.get("normal_pct", 0) or 0) / 100.0
            ref = float((tick or {}).get("last_price") or 0)
            if px > 0 and ref > 0 and pct > 0:
                if px > ref * (1 + pct) + 1e-6 or px < ref * (1 - pct) - 1e-6:
                    raise RuleViolation("PRICE_BAND", f"申报价 {px} 超出涨跌停带（ref={ref}, ±{pct:.0%}）")


def _is_reduce(intent: Any) -> bool:
    act = str(getattr(intent, "action", "") or "").upper()
    reason = str(getattr(intent, "reason", "") or "")
    return act in ("SELL", "REDUCE") or reason == "emergency_close_all"


def rules_for(account_id: int | None, symbol: str, provider: str | None = None,
              tick: dict | None = None) -> RuleSet:
    """版本缓存工厂：规格缺失返回 state='missing' 的 RuleSet（不抛，交调用方分流）。"""
    from src.data_platform import specs

    # 账户策略先解析（close_policy 不依赖品种行）
    acc = None
    pol = None
    if account_id is not None:
        try:
            acc = specs.AccountPermSpec.of(account_id)
            pol = specs.get_account_close_policy(account_id)
        except Exception as e:
            logger.warning("规则加载失败（account %s）: %s", account_id, e)
    if pol:
        from dataclasses import replace
        acc = replace(acc, close_policy=pol) if acc is not None else None
    if acc is None or not getattr(acc, "close_policy", None):
        dflt = PROVIDER_CLOSE_DEFAULTS.get(provider or "", _DEFAULT_CLOSE)
        if acc is None:
            acc = _DummyAcc(dict(dflt))
        else:
            from dataclasses import replace
            acc = replace(acc, close_policy=dict(dflt))

    try:
        ins = specs.InstrumentSpec.of(symbol)
    except Exception as e:
        logger.warning("规则加载失败（instrument）%s: %s", symbol, e)
        return RuleSet(ms=None, ins=_EMPTY_INS(symbol), acc=acc, state="missing")

    try:
        ms = specs.MarketSpec.of(ins.market)
    except Exception:
        ms = None
    stale = specs.is_stale("instrument", symbol) or (
        account_id is not None and specs.is_stale("perm", str(account_id)))
    return RuleSet(ms=ms, ins=ins, acc=acc, state="stale" if stale else "fresh")


@dataclass(frozen=True)
class _DummyAcc:
    close_policy: dict


def _EMPTY_INS(symbol: str) -> Any:
    return type("_Ins", (), {
        "symbol": symbol, "market": "", "tick_size": Decimal("0"), "lot_size": 1,
        "price_band": None, "sellable_rule": "volume_minus_frozen", "order_types": [],
        "position_side": "long_only", "venue_flags": {}, "t_plus": 1, "order_range": None,
    })()


def assert_dynamic_rules_complete() -> None:
    """启动校验：必需 category 的动态规则必须已注册（缺失=规则静默失效）。"""
    missing = [c for c in REQUIRED_DYNAMIC_CATEGORIES if c not in _DYNAMIC_RULES]
    if missing:
        raise RuntimeError(f"动态规则未注册: {missing}（规则层不完整，拒绝启动）")