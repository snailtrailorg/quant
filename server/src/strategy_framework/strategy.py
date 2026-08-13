"""策略框架 · Strategy 基类 + 信号聚合 + Python 代码模式。

所有策略（A股分析/可转债ETF/加密合约）共用此基类，差异下沉到 ExecutionAdapter。
Python 代码模式（#15）允许用户写自定义 on_bar 逻辑，替代 DSL 因子组合。
"""

from __future__ import annotations
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable
from .factor import Factor, BarContext, list_factors, get_factor, DSLFactor, _FACTOR_REGISTRY, _check_ast_blacklist
from .broker import record_broker_usage

logger = logging.getLogger("strategy")


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
    # ActionSignal 扩展（#3，策略表单可配）
    volume_type: str = "SHARES"  # SHARES（股数）/ PERCENT（资金百分比，需账户资产）/ ALL_IN（全仓）
    price_type: str = "LIMIT"    # MARKET（市价）/ LIMIT（限价）
    order_validity: str = "DAY"  # DAY / GTC


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

# --- 参数定义系统（parameter_defs 校验 + 默认值合并） ---

_PARAM_TYPES = {"number", "boolean", "string", "select"}


def validate_parameter_defs(defs):
    """校验 parameter_defs 结构。返回错误描述，None 表示合法。"""
    if not isinstance(defs, list):
        return "parameter_defs 必须是数组"
    names = set()
    for d in defs:
        if not isinstance(d, dict):
            return "参数定义必须是对象"
        name = d.get("name")
        if not name or not isinstance(name, str):
            return "参数定义缺 name 字段"
        if name in names:
            return f"参数名重复: {name}"
        names.add(name)
        ptype = d.get("type", "number")
        if ptype not in _PARAM_TYPES:
            return f"参数 {name} 类型不支持: {ptype}（可选: {', '.join(_PARAM_TYPES)}）"
        if "default" not in d:
            return f"参数 {name} 缺 default 字段"
        if ptype == "select" and not d.get("options"):
            return f"select 参数 {name} 缺 options"
    return None


def build_default_params(defs):
    """从 parameter_defs 构建默认参数值 dict。"""
    result = {}
    for d in (defs or []):
        v = d.get("default")
        if v is None:
            continue
        result[d["name"]] = v
    return result


def validate_params_against_defs(params, defs):
    """按 parameter_defs 校验参数值类型/范围。返回错误描述，None 表示合法。"""
    defs_by_name = {d["name"]: d for d in (defs or [])}
    for name, val in params.items():
        if name == "parameter_defs":
            continue
        if name not in defs_by_name:
            continue
        d = defs_by_name[name]
        ptype = d.get("type", "number")
        if ptype == "number":
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                return f"参数 {name} 必须是数字，实际: {type(val).__name__}"
            if "min" in d and val < d["min"]:
                return f"参数 {name}={val} 低于最小值 {d['min']}"
            if "max" in d and val > d["max"]:
                return f"参数 {name}={val} 超过最大值 {d['max']}"
        elif ptype == "boolean":
            if not isinstance(val, bool):
                return f"参数 {name} 必须是布尔，实际: {type(val).__name__}"
        elif ptype == "string":
            if not isinstance(val, str):
                return f"参数 {name} 必须是字符串，实际: {type(val).__name__}"
        elif ptype == "select":
            valid_vals = [o.get("value") for o in d.get("options", [])]
            if val not in valid_vals:
                return f"参数 {name}={val} 不在可选项 {valid_vals}"
    return None


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
                logger.warning("因子 %s 计算失败: %s", f.name, e)
                result[f.name] = 0.0
        return result

    # ——— 下单 ———

    def place_order(self, sig: Signal) -> None:
        """下单：前置风控 → ExecutionAdapter。"""
        from ..risk_control.risk import RiskControl  # 延迟导入
        order = {
            "symbol": self.symbol,
            "action": sig.action.name,
            "volume": sig.volume if sig.volume is not None else 100,
            "price": sig.price if sig.price is not None else 0,
            "reason": sig.reason,
        }
        decision = RiskControl.get().check_order(order, "")
        if not decision.approved:
            return
        final = decision.adjusted if decision.adjusted is not None else order  # B8 风控覆写：用 adjusted（如截断 volume），无则原值
        from .adapters import Order
        import time as _t
        _t0 = _t.time()
        try:
            self.adapter.send_order(Order(
                symbol=final.get("symbol", self.symbol),
                action=final.get("action", sig.action.name),
                volume=final.get("volume", sig.volume if sig.volume is not None else 100),
                price=final.get("price", sig.price if sig.price is not None else 0),
                order_type="market" if sig.price_type == "MARKET" else "limit",
            ))
        except Exception:
            record_broker_usage(self.config.adapter, sig.action.name, self.symbol,
                                success=False, latency_ms=int((_t.time() - _t0) * 1000))
            raise
        else:
            record_broker_usage(self.config.adapter, sig.action.name, self.symbol,
                                success=True, latency_ms=int((_t.time() - _t0) * 1000))
            self._log_signal_order(sig, final)  # P1-3 三账数据来源


    def _log_signal_order(self, sig: Signal, final_order: dict) -> None:
        """写 signal_log + order_log（三账对账数据来源，P1-3）。回测 monkey-patch 跳过。"""
        try:
            from ..data_platform.db import get_conn
            with get_conn() as conn:
                conn.execute("SELECT 1 FROM signal_log LIMIT 1")
                conn.execute("SELECT 1 FROM order_log LIMIT 1")
                cur = conn.execute(
                    "INSERT INTO signal_log (strategy_id,symbol,action,score,price) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (self.config.id, self.symbol, sig.action.name, sig.score, sig.price))
                sig_id = cur.fetchone()[0]
                conn.execute(
                    "INSERT INTO order_log (strategy_id,symbol,action,volume,price,signal_id) VALUES (%s,%s,%s,%s,%s,%s)",
                    (self.config.id, self.symbol, final_order.get("action", sig.action.name),
                     final_order.get("volume", 100), final_order.get("price", 0), sig_id))
                conn.commit()
        except Exception as e:
            logger.warning("记录 signal/order 日志失败: %s", e)

    # ——— 工厂 ———

    @classmethod
    def from_config(cls, config: StrategyConfig, adapter) -> "Strategy":
        """P1-1 策略类型注册制：按 config.type 分发到注册的子类，默认基类。

        Python 代码模式（#15）优先：params.mode == "python" 时返回 PythonStrategy。
        """
        if config.params.get("mode") == "python":
            return PythonStrategy(config, adapter)
        strategy_cls = _STRATEGY_REGISTRY.get(config.type, cls)
        return strategy_cls(config, adapter)


# 策略类型注册表 + 装饰器（P1-1）
_STRATEGY_REGISTRY: dict[str, type] = {}


def register_strategy(type_name: str):
    """策略子类注册装饰器。from_config 按 type 查此表分发。"""
    def decorator(cls):
        _STRATEGY_REGISTRY[type_name] = cls
        return cls
    return decorator


# ——— Python 代码模式（#15） ———

class StrategyContext:
    """Python 代码模式中用户代码唯一可用的上下文。

    用户 on_bar 函数接收此对象，通过它访问数据、下单、管理状态。
    """

    def __init__(self, strategy_id: str, symbol: str):
        self.id = strategy_id
        self.symbol = symbol
        self._bar: dict = {}
        self._history: list[dict] = []
        self._params: dict = {}
        self._state: dict = {}
        self._signal: Signal | None = None

    def _update(self, bar: dict, history: list[dict] | None, params: dict):
        self._bar = bar
        self._history = history or []
        self._params = params
        self._signal = None
        # 构建 BarContext 供 get_factor 调用
        self._bar_ctx = BarContext(
            close=bar.get("close", 0),
            high=bar.get("high", 0),
            low=bar.get("low", 0),
            open_=bar.get("open", 0),
            volume=bar.get("volume", 0),
            history=history or [],
        )

    # ——— 只读数据 ———

    def get_bar(self, field: str = "close", default: float = 0.0) -> float:
        """取当前 bar 的字段值。字段: close/open/high/low/volume/ts"""
        return self._bar.get(field, default)

    def get_history(self, n: int = 20) -> list[float]:
        """取最近 n 根 bar 的 close 价格列表（不含当前 bar）。"""
        closes = [h.get("close", 0) for h in self._history[-n:]] if self._history else []
        return closes

    def get_full_history(self, n: int = 20) -> list[dict]:
        """取最近 n 根 bar 的完整 dict 列表。"""
        return self._history[-n:] if self._history else []

    def get_param(self, key: str, default=None):
        """取策略参数（由 Web 编辑页配置）。"""
        return self._params.get(key, default)

    # ——— 下单 ———

    def buy(self, volume: float = 100, price_type: str = "LIMIT") -> Signal:
        """买入信号。仍走风控 check_order 前置。"""
        self._signal = Signal(
            action=Action.BUY, symbol=self.symbol, volume=volume,
            price=self._bar.get("close", 0), price_type=price_type,
            reason="Python 策略买入",
        )
        return self._signal

    def sell(self, volume: float = 100, price_type: str = "LIMIT") -> Signal:
        """卖出信号。"""
        self._signal = Signal(
            action=Action.SELL, symbol=self.symbol, volume=volume,
            price=self._bar.get("close", 0), price_type=price_type,
            reason="Python 策略卖出",
        )
        return self._signal

    def hold(self, reason: str = "") -> Signal:
        """持仓不动。"""
        self._signal = Signal(action=Action.HOLD, symbol=self.symbol, reason=reason or "Python 策略持有")
        return self._signal

    # ——— 状态管理 ———

    def set_state(self, key: str, value):
        """保存运行时状态（跨 tick 持久，仅内存）。"""
        self._state[key] = value

    def get_state(self, key: str, default=None):
        """读取运行时状态。"""
        return self._state.get(key, default)

    # ——— 因子调用（双通道：Python 策略也可调注册因子） ———

    def get_factor(self, name: str, **kwargs) -> float:
        """调用注册因子，返回因子值。

        用于 Python 模式策略中调用预置或自定义因子：
            ma_dev = ctx.get_factor("ma_dev", n=20)
        """
        entry = get_factor(name)
        if entry is None:
            raise ValueError(f"未知因子: {name}")
        factor = entry["cls"]()
        factor.params = {**entry.get("params", {}), **kwargs}
        return factor.compute(self._bar_ctx)


class PythonStrategy(Strategy):
    """Python 代码模式策略（#15）。

    用户通过 Web 编辑器写 Python 代码，定义 on_bar(ctx) 函数。
    代码在受限 namespace 中 exec，只可访问 ctx 对象。
    """

    def __init__(self, config: StrategyConfig, adapter):
        super().__init__(config, adapter)
        self._user_code = config.params.get("python_code", "")
        self._ctx = StrategyContext(config.id, config.symbol)
        self._compiled = None
        if self._user_code:
            try:
                self._compiled = compile(self._user_code, "<strategy>", "exec")
            except SyntaxError as e:
                raise ValueError(f"Python 策略代码语法错误: {e}")

    def on_bar(self, bar: dict, history: list[dict] | None = None) -> Signal | None:
        """Python 模式 on_bar：exec 用户代码，调用户 on_bar(ctx)。

        安全：exec 在受限 namespace 中运行，只暴露 ctx 对象和安全的 builtins 子集。
        """
        self._ctx._update(bar, history or [], self.config.params)
        # 受限 namespace：只暴露 ctx + on_bar + 安全 builtins 子集
        safe_builtins = {
            "abs": abs, "max": max, "min": min, "sum": sum,
            "round": round, "float": float, "int": int,
            "len": len, "range": range, "list": list, "dict": dict,
            "str": str, "bool": bool, "True": True, "False": False, "None": None,
            "print": print,  # print 无害（stdout 到 journal）
        }
        namespace = {"ctx": self._ctx, "on_bar": None, "__builtins__": safe_builtins}
        if self._compiled:
            exec(self._compiled, namespace)
        user_on_bar = namespace.get("on_bar")
        if user_on_bar:
            sig = user_on_bar(self._ctx)
            # 如果用户函数返回 None，取 ctx._signal
            if sig is None:
                sig = self._ctx._signal
            if sig and sig.action != Action.HOLD:
                self.place_order(sig)
            return sig
        return None