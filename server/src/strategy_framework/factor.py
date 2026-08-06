"""策略框架 · 因子注册制 + DSL 表达式引擎。

因子统一接口，`@register_factor` 装饰器自注册到全局注册表，Web 端可选。
"""

from __future__ import annotations
import ast
import operator as op
from typing import Any, Callable, ClassVar
from dataclasses import dataclass, field


# ——— 因子注册表 ———

_FACTOR_REGISTRY: dict[str, dict] = {}


def register_factor(name: str, *, category: str = "custom", **kwargs):
    """装饰器：将因子类注册到全局注册表。

    Usage:
        @register_factor("ma_dev", category="trend", params={"n": 20})
        class MADevFactor(Factor):
            ...
    """
    def wrapper(cls):
        _FACTOR_REGISTRY[name] = {
            "cls": cls,
            "name": name,
            "category": category,
            "params": kwargs.get("params", {}),
            "description": kwargs.get("description", cls.__doc__ or ""),
        }
        return cls
    return wrapper


def list_factors(category: str | None = None) -> list[dict]:
    """列出已注册的因子，可选按 category 过滤。"""
    items = list(_FACTOR_REGISTRY.values())
    if category:
        items = [i for i in items if i["category"] == category]
    return items


def get_factor(name: str) -> dict | None:
    return _FACTOR_REGISTRY.get(name)


# ——— Factor 基类 ———

class BarContext:
    """因子计算上下文（从 K 线数据构建）。"""
    def __init__(self, close: float, high: float, low: float,
                 open_: float, volume: float, history: list[dict] | None = None):
        self.close = close
        self.high = high
        self.low = low
        self.open_ = open_
        self.volume = volume
        self._history = history or []

    def sma(self, n: int) -> float:
        """简单移动平均：取最近 n 根 K 线收盘价均值（含当前）。"""
        closes = [h.get("close", 0) for h in self._history[-(n-1):]] if self._history else []
        closes.append(self.close)
        if len(closes) < n:
            return sum(closes) / len(closes) if closes else self.close
        return sum(closes[-n:]) / n

    @property
    def history(self) -> list[dict]:
        return self._history


class Factor:
    """因子组件基类。"""
    name: str = ""
    params: dict = field(default_factory=dict)

    def compute(self, ctx: BarContext) -> float:
        raise NotImplementedError


# ——— DSL 表达式因子（安全 eval） ———

# 白名单操作符
_DT_OPS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
    ast.Div: op.truediv, ast.Pow: op.pow,
    ast.USub: op.neg, ast.UAdd: op.pos,
    ast.Mod: op.mod, ast.FloorDiv: op.floordiv,
}

# 白名单函数
_DT_FUNCS = {
    "abs": abs, "max": max, "min": min, "sum": sum,
    "round": round, "float": float, "int": int,
}


def _safe_eval(expr: str, ctx: dict[str, float]) -> float:
    """安全 eval：AST 白名单，只允许算术+索引+白名单函数。"""
    if len(expr) > 500:  # 表达式长度限制
        raise ValueError("表达式过长")
    tree = ast.parse(expr, mode="eval")

    def _eval(node):
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.Name):
            if node.id in ctx:
                return ctx[node.id]
            if node.id in _DT_FUNCS:
                return _DT_FUNCS[node.id]
            raise NameError(f"未知变量/函数: {node.id}")
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _DT_OPS:
                raise TypeError(f"不支持的运算符: {type(node.op).__name__}")
            return _DT_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        elif isinstance(node, ast.UnaryOp):
            if type(node.op) not in _DT_OPS:
                raise TypeError(f"不支持的一元运算符: {type(node.op).__name__}")
            return _DT_OPS[type(node.op)](_eval(node.operand))
        elif isinstance(node, ast.Call):
            func = _eval(node.func)
            args = [_eval(a) for a in node.args]
            return func(*args)
        elif isinstance(node, ast.Attribute):
            return getattr(_eval(node.value), node.attr)
        raise TypeError(f"不支持的表达式节点: {type(node).__name__}")

    return float(_eval(tree.body))


@register_factor("dsl", category="custom", description="受限 DSL 表达式因子")
class DSLFactor(Factor):
    """DSL 表达式因子：Web 端写受限表达式，如 'mean(close,20) / close - 1'。"""

    def __init__(self, name: str, expr: str):
        super().__init__()
        self.name = name
        self.expr = expr
        self.params = {"expr": expr}

    def compute(self, ctx: BarContext) -> float:
        return _safe_eval(self.expr, {
            "close": ctx.close, "high": ctx.high, "low": ctx.low,
            "open_": ctx.open_, "volume": ctx.volume,
        })


# ——— 预置因子示例 ———

@register_factor("ma_dev", category="trend", params={"n": 20},
                 description="均线偏离度: close / sma(close,n) - 1")
class MADevFactor(Factor):
    def compute(self, ctx: BarContext) -> float:
        return ctx.close / ctx.sma(self.params.get("n", 20)) - 1


@register_factor("rsi", category="trend", params={"n": 14})
class RSIFactor(Factor):
    def compute(self, ctx: BarContext) -> float:
        # 简化实现
        return 50.0


@register_factor("volume_ratio", category="trend", params={"n": 5})
class VolumeRatioFactor(Factor):
    def compute(self, ctx: BarContext) -> float:
        return ctx.volume / max(ctx.sma(self.params.get("n", 5)), 1)


@register_factor("double_low", category="convertible",
                 description="可转债双低: price + premium_rate * 100")
class DoubleLowFactor(Factor):
    def compute(self, ctx: BarContext) -> float:
        return -ctx.close  # 简化：价格越低越好（负值，后续聚合反向）


@register_factor("funding_rate", category="crypto",
                 description="资金费率因子")
class FundingRateFactor(Factor):
    def compute(self, ctx: BarContext) -> float:
        return 0.0

# --- 因子跨品类兼容性校验 ---

# 市场品类 -> 可用因子 category 映射
CATEGORY_COMPAT = {
    "astock":     ["trend", "fundamental", "meanrev"],
    "convertible": ["trend", "convertible", "meanrev"],
    "etf":        ["trend", "meanrev"],
    "crypto":     ["trend", "crypto", "meanrev"],
    "unknown":    ["trend", "meanrev"],
}

# 因子 -> 品类专用（只限该品类可用）
FACTOR_EXCLUSIVE = {
    "double_low": "convertible",
    "funding_rate": "crypto",
}


def detect_category(vt_symbol: str) -> str:
    """从 vt_symbol 推断市场品类。"""
    sym = vt_symbol.upper()
    if any(x in sym for x in (".BINANCE", ".OKX", "PERP")):
        return "crypto"
    if sym.startswith("11") or sym.startswith("12") or "CB" in sym:
        return "convertible"
    if sym.startswith("15") or sym.startswith("16") or sym.startswith("51") or sym.startswith("52"):
        return "etf"
    if any(x in sym for x in (".SHSE", ".SZSE", ".BSE")):
        return "astock"
    return "unknown"


def filter_factors_by_category(factors: list[dict], category: str) -> tuple[list[dict], list[dict]]:
    """按品类过滤因子。

    Returns: (compatible, incompatible)
    """
    allowed_cats = CATEGORY_COMPAT.get(category, ["trend", "meanrev"])
    compatible = []
    incompatible = []
    for f in factors:
        name = f.get("name", "").replace("dsl:", "")
        # 专用因子检查
        if name in FACTOR_EXCLUSIVE:
            if FACTOR_EXCLUSIVE[name] == category:
                compatible.append(f)
            else:
                incompatible.append(f)
            continue
        # 按 category 检查
        entry = get_factor(name)
        if entry:
            if entry["category"] in allowed_cats:
                compatible.append(f)
            else:
                incompatible.append(f)
        else:
            # DSL 或未知因子，默认兼容
            compatible.append(f)
    return compatible, incompatible


def validate_strategy_factors(vt_symbol: str, factor_configs: list[dict]) -> dict:
    """校验策略配置的因子是否与标的品类兼容。

    Returns: {"valid": bool, "category": str, "compatible": [...], "incompatible": [...]}
    """
    category = detect_category(vt_symbol)
    compatible, incompatible = filter_factors_by_category(factor_configs, category)
    return {
        "valid": len(incompatible) == 0,
        "category": category,
        "compatible": compatible,
        "incompatible": incompatible,
        "message": f"标的品类={category}，不兼容因子: {[f['name'] for f in incompatible]}" if incompatible else "全部兼容",
    }
