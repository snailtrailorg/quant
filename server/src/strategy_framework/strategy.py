"""策略框架 · Strategy 基类 + 信号聚合。

所有策略（A股分析/可转债ETF/加密合约）共用此基类，差异下沉到 ExecutionAdapter。
"""

from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable
from .factor import Factor, BarContext, list_factors, get_factor, DSLFactor, _FACTOR_REGISTRY


# ——— 信号定义 ———

class Action(Enum):
    BUY = 1
    SELL = 2
    HOLD = 0


@dataclass
class Signal:
    action: Action
    score: float = 0.0
    symbol: str = ""
    volume: float = 0.0
    price: float = 0.0
    reason: str = ""


# ——— 信号聚合 ———

@dataclass
class SignalAggregator:
    weights: dict[str, float] = field(default_factory=dict)
    threshold_buy: float = 0.3
    threshold_sell: float = -0.3
    method: str = "weighted_sum"

    def aggregate(self, factor_values: dict[str, float]) -> Signal:
        """因子值 → 买卖信号。"""
        score = 0.0
        for name, val in factor_values.items():
            w = self.weights.get(name, 1.0)
            score += val * w

        if score > self.threshold_buy:
            return Signal(action=Action.BUY, score=score, reason=f"score={score:.3f} > {self.threshold_buy}")
        elif score < self.threshold_sell:
            return Signal(action=Action.SELL, score=score, reason=f"score={score:.3f} < {self.threshold_sell}")
        return Signal(action=Action.HOLD, score=score)


# ——— 策略配置 Schema ———

@dataclass
class StrategyConfig:
    id: str
    name: str
    type: str  # "astock_analysis" / "convertible_t0" / "crypto_perp"
    symbol: str
    adapter: str  # "xtp"（可转债/ETF/A股股票，中泰XTP）/ "binance_perp" / "okx_perp"
    enabled: bool = True
    factors: list[dict] = field(default_factory=list)  # [{"name":"ma_dev","weight":0.6,"params":{}}, ...]
    aggregator: dict = field(default_factory=lambda: {"method":"weighted_sum","threshold_buy":0.3,"threshold_sell":-0.3})
    risk: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)


# ——— Strategy 基类 ———

class Strategy:
    """统一策略基类。所有策略（A股分析/可转债 ETF/加密合约）继承此基类。"""

    def __init__(self, config: StrategyConfig, adapter):
        self.id = config.id
        self.symbol = config.symbol
        self.config = config
        self.adapter = adapter
        self._factors: list[Factor] = []
        self._aggregator = SignalAggregator(
            weights={f["name"]: f.get("weight", 1.0) for f in config.factors},
            method=config.aggregator.get("method", "weighted_sum"),
            threshold_buy=config.aggregator.get("threshold_buy", 0.3),
            threshold_sell=config.aggregator.get("threshold_sell", -0.3),
        )
        self._init_factors(config.factors)

    def _init_factors(self, factor_configs: list[dict]):
        """从配置初始化因子实例。"""
        for fc in factor_configs:
            name = fc["name"]
            if name.startswith("dsl:"):
                # DSL 表达式因子
                expr = fc.get("expr", "")
                factor = DSLFactor(name, expr)
            else:
                entry = get_factor(name)
                if entry is None:
                    raise ValueError(f"未知因子: {name}")
                factor = entry["cls"]()
                factor.name = name
                factor.params = {**entry["params"], **fc.get("params", {})}
            self._factors.append(factor)

    # ——— 核心回调 ———

    def on_bar(self, bar: dict, history: list[dict] | None = None) -> Signal | None:
        """收到 K 线 → 因子计算 → 信号 → 下单。"""
        ctx = BarContext(
            close=bar.get("close", 0),
            high=bar.get("high", 0),
            low=bar.get("low", 0),
            open_=bar.get("open", 0),
            volume=bar.get("volume", 0),
            history=history or [],
        )
        fv = self.compute_factors(ctx)
        sig = self._aggregator.aggregate(fv)
        sig.symbol = self.symbol
        if sig.action != Action.HOLD:
            self.place_order(sig)
        return sig

    def on_tick(self, tick: dict) -> None:
        """收到 Tick → 实时处理。"""
        pass

    # ——— 因子计算 ———

    def compute_factors(self, ctx: BarContext) -> dict[str, float]:
        """计算所有因子值。"""
        result = {}
        for f in self._factors:
            try:
                result[f.name] = f.compute(ctx)
            except Exception as e:
                result[f.name] = 0.0
        return result

    # ——— 下单 ———

    def place_order(self, sig: Signal) -> None:
        """下单：前置风控 → ExecutionAdapter。"""
        from ..risk_control.risk import RiskControl  # 延迟导入
        order = {
            "symbol": self.symbol,
            "action": sig.action.name,
            "volume": sig.volume or 100,
            "price": sig.price or 0,
            "reason": sig.reason,
        }
        decision = RiskControl.get().check_order(order, "")
        if not decision.approved:
            return
        # Order dataclass 给 adapter（类型契约一致；check_order 收 dict）
        from .adapters import Order
        self.adapter.send_order(Order(
            symbol=self.symbol,
            action=sig.action.name,
            volume=sig.volume or 100,
            price=sig.price or 0,
            order_type="limit",
        ))

    # ——— 工厂 ———

    @classmethod
    def from_config(cls, config: StrategyConfig, adapter) -> "Strategy":
        return cls(config, adapter)