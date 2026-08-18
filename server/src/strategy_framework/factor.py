"""策略框架 · 因子注册制 + DSL 表达式引擎 + 自定义因子（DB 化）。

因子统一接口，`@register_factor` 装饰器自注册预置因子。
用户可在 Web 写 Python 代码创建自定义因子，保存在 `factor_def` 表。
预置因子和自定义因子统一注册到 `_FACTOR_REGISTRY`，DSL/Python 模式无差别调用。
"""

from __future__ import annotations
import ast
import operator as op
from typing import Any, Callable, ClassVar
from dataclasses import dataclass, field


# ——— AST 安全校验（#15 Python 代码框 + 自定义因子） ———

_FORBIDDEN_BUILTINS = frozenset({
    "exec", "eval", "compile", "open", "__import__",
    "globals", "locals", "vars", "dir",
    "getattr", "setattr", "delattr", "hasattr",
    "__builtins__", "__build_class__",
})


class _AstBlacklistChecker(ast.NodeVisitor):
    def visit_Import(self, node):
        raise ValueError("禁止 import")
    def visit_ImportFrom(self, node):
        raise ValueError("禁止 from ... import")


def _check_ast_blacklist(code: str) -> str | None:
    """检查 Python 代码的 AST 安全。返回违规描述，None 表示安全。"""
    try:
        tree = ast.parse(code, "<factor>", "exec")
        _AstBlacklistChecker().visit(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if node.id in _FORBIDDEN_BUILTINS:
                    return f"禁止使用: {node.id}"
        return None
    except SyntaxError as e:
        return f"语法错误: {e}"
    except ValueError as e:
        return str(e)


# ——— 因子注册表 ———

_FACTOR_REGISTRY: dict[str, dict] = {}


def register_factor(name: str, *, category: str = "custom", needs_history: int = 0, **kwargs):
    """装饰器：将因子类注册到全局注册表。

    needs_history: 需要的历史窗口大小。0=静态因子（只用当前 bar，可选股+策略）；
                   >0=动态因子（需要历史窗口，只能用于策略，不能选股）。

    Usage:
        @register_factor("ma_dev", category="trend", params={"n": 20}, needs_history=20)
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
            "is_custom": False,
            "needs_history": needs_history,
        }
        return cls
    return wrapper


def list_factors(category: str | None = None, static_only: bool = False) -> list[dict]:
    """列出已注册的因子。

    category: 按 category 过滤
    static_only: True 只返回静态因子（needs_history=0），用于选股引擎
    """
    items = list(_FACTOR_REGISTRY.values())
    if category:
        items = [i for i in items if i["category"] == category]
    if static_only:
        items = [i for i in items if i.get("needs_history", 0) == 0]
    return items


def get_factor(name: str) -> dict | None:
    return _FACTOR_REGISTRY.get(name)


# ——— 自定义因子：DB 加载 + 编译 ———

# 安全 builtins 子集（用户自定义因子 compute 函数可用）
_FACTOR_SAFE_BUILTINS = {
    "abs": abs, "max": max, "min": min, "sum": sum,
    "round": round, "float": float, "int": int,
    "len": len, "range": range, "list": list, "dict": dict,
    "str": str, "bool": bool, "True": True, "False": False, "None": None,
    "print": print,
}

import logging
_logger = logging.getLogger("factor")


def _make_factor_class(name: str, code: str, default_params: dict) -> type:
    """编译用户 Python 代码为 Factor 子类。

    用户代码定义 compute(ctx, **params) 函数：
        def compute(ctx, n=20):
            closes = [h.get("close", 0) for h in ctx.history[-n:]] + [ctx.close]
            return ctx.close / sum(closes) * n - 1
    """
    # 安全校验
    err = _check_ast_blacklist(code)
    if err:
        raise ValueError(f"因子代码安全校验失败: {err}")

    # 编译 + exec 在受限 namespace
    ns = {"__builtins__": _FACTOR_SAFE_BUILTINS}
    try:
        exec(code, ns)
    except Exception as e:
        raise ValueError(f"因子代码编译失败: {e}")

    if "compute" not in ns or not callable(ns["compute"]):
        raise ValueError("因子代码必须定义 compute(ctx, ...) 函数")

    # 动态创建 Factor 子类
    factor_cls = type(
        f"CustomFactor_{name}",
        (Factor,),
        {
            "name": name,
            "params": dict(default_params),
            "compute": staticmethod(ns["compute"]),
        },
    )
    return factor_cls


def load_factors_from_db() -> list[str]:
    """从 factor_def 表加载自定义因子到注册表。返回加载的因子名列表。"""
    loaded = []
    try:
        from ..data_platform.db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT name, category, description, code, params, needs_history FROM factor_def"
            ).fetchall()
        for name, category, description, code, params, needs_history in rows:
            import json
            params_dict = json.loads(params) if isinstance(params, str) else (params or {})
            try:
                factor_cls = _make_factor_class(name, code, params_dict)
                _FACTOR_REGISTRY[name] = {
                    "cls": factor_cls,
                    "name": name,
                    "category": category or "custom",
                    "params": params_dict,
                    "description": description or "",
                    "is_custom": True,
                    "needs_history": int(needs_history or 0),
                }
                loaded.append(name)
            except Exception as e:
                _logger.warning("加载自定义因子 %s 失败: %s", name, e)
    except Exception as e:
        _logger.warning("从 DB 加载因子失败（表可能未创建）: %s", e)
    return loaded


def register_custom_factor(name: str, category: str, code: str,
                            description: str = "", params: dict | None = None,
                            needs_history: int = 0) -> dict:
    """创建或更新自定义因子：编译代码 → 写 DB → 进注册表。

    Returns: {"id": int, "name": str, ...}
    """
    # 1. 编译校验（安全）
    factor_cls = _make_factor_class(name, code, params or {})

    # 2. 写 DB
    import json
    from ..data_platform.db import get_conn
    params_json = json.dumps(params or {})
    with get_conn() as conn:
        # UPSERT
        cur = conn.execute(
            "SELECT id FROM factor_def WHERE name=%s", (name,)
        )
        existing = cur.fetchone()
        if existing:
            conn.execute(
                "UPDATE factor_def SET category=%s, description=%s, code=%s, params=%s, needs_history=%s, updated_at=now() WHERE name=%s",
                (category, description, code, params_json, needs_history, name),
            )
            fid = existing[0]
        else:
            cur = conn.execute(
                "INSERT INTO factor_def (name, category, description, code, params, needs_history) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (name, category, description, code, params_json, needs_history),
            )
            fid = cur.fetchone()[0]
        conn.commit()

    # 3. 更新注册表
    _FACTOR_REGISTRY[name] = {
        "cls": factor_cls,
        "name": name,
        "category": category,
        "params": params or {},
        "description": description,
        "is_custom": True,
        "needs_history": needs_history,
    }
    return {"id": fid, "name": name, "category": category, "is_custom": True, "needs_history": needs_history}


def delete_custom_factor(name: str) -> bool:
    """删除自定义因子：从 DB 和注册表移除。"""
    # 只删除自定义因子，不碰预置因子
    entry = _FACTOR_REGISTRY.get(name)
    if not entry or not entry.get("is_custom"):
        return False
    from ..data_platform.db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM factor_def WHERE name=%s", (name,))
        conn.commit()
    _FACTOR_REGISTRY.pop(name, None)
    return True


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
        """简单移动平均。P4-1 缓存：同 bar 多次调 sma(n) 只算一次。"""
        cache_key = f"sma_{n}"
        if not hasattr(self, "_sma_cache"):
            self._sma_cache = {}
        if cache_key in self._sma_cache:
            return self._sma_cache[cache_key]
        closes = [h.get("close", 0) for h in self._history[-(n-1):]] if self._history else []
        closes.append(self.close)
        if len(closes) < n:
            result = sum(closes) / len(closes) if closes else self.close
        else:
            result = sum(closes[-n:]) / n
        self._sma_cache[cache_key] = result
        return result

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


# 链条打磨#8（2026-08-19）：DSL 历史窗口函数——表达式可引用序列（如 ma20 = mean(close,20)）。
# 语义：xxx 为当前值（float），xxx_n（下标访问不存在）——用函数形式 mean(close,20) 取
# 最近 20 根（含当前）close 的均值；序列源 = ctx.history + 当前 bar。
def _series(ctx: BarContext, name: str) -> list[float]:
    """从 ctx 提取命名序列（含当前 bar）。name: close/high/low/open/volume。"""
    key = "open" if name == "open" else name
    hist = [h.get(key, 0) for h in (ctx._history or [])]
    cur = getattr(ctx, key if key != "open" else "open_", 0) or 0
    return hist + [cur]


def _w_mean(seq: list, n: int) -> float:
    w = seq[-n:] if n and n > 0 else seq
    return sum(w) / len(w) if w else 0.0


def _w_std(seq: list, n: int) -> float:
    w = seq[-n:] if n and n > 0 else seq
    if len(w) < 2:
        return 0.0
    m = sum(w) / len(w)
    return (sum((x - m) ** 2 for x in w) / len(w)) ** 0.5


def _w_max(seq: list, n: int) -> float:
    return max(seq[-n:]) if n and n > 0 else (max(seq) if seq else 0.0)


def _w_min(seq: list, n: int) -> float:
    return min(seq[-n:]) if n and n > 0 else (min(seq) if seq else 0.0)


def _w_ema(seq: list, n: int) -> float:
    w = seq[-(n * 2):] if n and n > 0 else seq
    if not w:
        return 0.0
    k = 2 / (n + 1)
    e = w[0]
    for x in w[1:]:
        e = x * k + e * (1 - k)
    return e


def _w_rsi(seq: list, n: int) -> float:
    w = seq[-(n + 1):] if n and n > 0 else seq
    if len(w) < 2:
        return 50.0
    gains = losses = 0.0
    for i in range(1, len(w)):
        d = w[i] - w[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    if gains + losses == 0:
        return 50.0
    return 100 * gains / (gains + losses)


def _w_slope(seq: list, n: int) -> float:
    w = seq[-n:] if n and n > 0 else seq
    if len(w) < 2:
        return 0.0
    k = len(w)
    xs = list(range(k))
    mx = sum(xs) / k
    my = sum(w) / k
    num = sum((xs[i] - mx) * (w[i] - my) for i in range(k))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


def _w_avevol(seq: list, n: int) -> float:
    """平均换手率近似（volume 序列的均值比当前）。"""
    return _w_mean(seq, n)


_DSL_WINDOW_FUNCS = {"mean": _w_mean, "std": _w_std, "max": _w_max, "min": _w_min,
                     "ema": _w_ema, "rsi": _w_rsi, "slope": _w_slope, "avevol": _w_avevol}


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
            if node.keywords:
                # R-F2：kwargs 不再静默丢弃——白名单内转关键字参（mean(close,n=3)）
                kwnames = [kw.arg for kw in node.keywords]
                if any(k is None for k in kwnames) or len(set(kwnames)) != len(kwnames):
                    raise TypeError("DSL 不支持 **kwargs")
                kw_args = [_eval(kw.value) for kw in node.keywords]
                return func(*args, **dict(zip(kwnames, kw_args)))
            return func(*args)
        raise TypeError(f"不支持的表达式节点: {type(node).__name__}")

    return float(_eval(tree.body))


@register_factor("dsl", category="custom", description="受限 DSL 表达式因子", needs_history=0)
class DSLFactor(Factor):
    """DSL 表达式因子：Web 端写受限表达式，如 'mean(close,20) / close - 1'。"""

    def __init__(self, name: str, expr: str):
        super().__init__()
        self.name = name
        self.expr = expr
        self.params = {"expr": expr}

    def compute(self, ctx: BarContext) -> float:
        # 链条打磨#8：窗口函数（mean(close,20) 取 ctx.history 序列含当前 bar）。
        # AST 预处理：窗口调用第一参的裸 Name（close 等）改写为字符串字面量——
        # 纯算术处 Name 不动（close 仍是当前标量）；`close` 名与值由此二义消解。
        tree = ast.parse(self.expr, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in _DSL_WINDOW_FUNCS and node.args:
                a0 = node.args[0]
                if isinstance(a0, ast.Call):
                    raise TypeError(f"DSL 窗口函数不支持嵌套: {node.func.id}({ast.unparse(a0)})——写成自定义因子")
                if isinstance(a0, ast.Name):
                    node.args[0] = ast.Constant(value=a0.id)
        expr = ast.unparse(tree)
        env: dict = {
            "close": ctx.close, "high": ctx.high, "low": ctx.low,
            "open_": ctx.open_, "volume": ctx.volume,
        }
        for fname, fn in _DSL_WINDOW_FUNCS.items():
            def _mk(fname_=fname, f=fn):
                def _call(name_or_val, n=None):
                    # R-F2：静默错值比崩溃更危险——非序列名/未知名一律抛
                    if not isinstance(name_or_val, str):
                        raise TypeError(
                            f"{fname_}() 第一参必须是字段名（close/high/low/open/volume），"
                            f"收到表达式值 {name_or_val!r}——如需对表达式取窗口，先写成自定义因子")
                    if name_or_val not in ("close", "high", "low", "open", "volume"):
                        raise NameError(f"{fname_}() 未知字段: {name_or_val}")
                    seq = _series(ctx, name_or_val)
                    return f(seq, n) if n else f(seq, 0)
                return _call
            env[fname] = _mk()
        return _safe_eval(expr, env)


# ——— 预置因子 ———

@register_factor("ma_dev", category="trend", params={"n": 20}, needs_history=20,
                 description="均线偏离度: close / sma(close,n) - 1")
class MADevFactor(Factor):
    def compute(self, ctx: BarContext) -> float:
        return ctx.close / ctx.sma(self.params.get("n", 20)) - 1


@register_factor("rsi", category="trend", params={"n": 14}, needs_history=14)
class RSIFactor(Factor):
    def compute(self, ctx: BarContext) -> float:
        n = self.params.get("n", 14)
        closes = [h.get("close", 0) for h in ctx.history[-n:]] + [ctx.close]
        if len(closes) < 2:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses) if sum(losses) > 0 else 0.0001
        return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


@register_factor("volume_ratio", category="trend", params={"n": 5}, needs_history=5)
class VolumeRatioFactor(Factor):
    def compute(self, ctx: BarContext) -> float:
        return ctx.volume / max(ctx.sma(self.params.get("n", 5)), 1)


@register_factor("double_low", category="convertible", needs_history=0,
                 description="可转债双低: price + 溢价率×100（转股价值=100×正股昨收/转股价，convertible_terms+daily_basic）")
class DoubleLowFactor(Factor):
    def compute(self, ctx: BarContext) -> float:
        # 链条打磨#3（2026-08-19）：真实现替代 -close 占位。
        # 双低 = 转债价格 + (转债价格/转股价值 − 1)×100；转股价值 = 100 × 正股昨收 / 转股价。
        # 数据：convertible_terms.terms JSON {conv_price, stk_code} + bar_1d 正股最近 close。
        # 查不到条款/正股价 → 返回价格本身（溢价项记 0——低价格债券仍排前，弱化但可用，告警一次）。
        import json as _json
        from ..data_platform.db import get_conn
        from ..data_platform.schema import to_vt_symbol
        sym = getattr(ctx, "symbol", None) or ""
        if not sym:
            return ctx.close
        try:
            with get_conn() as conn:
                cur = conn.execute("SELECT terms FROM convertible_terms WHERE ts_code=%s", (sym,))
                row = cur.fetchone()
                if not row or not row[0]:
                    return ctx.close
                terms = _json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
                conv_price = float(terms.get("conv_price") or 0)
                stk = terms.get("stk_code") or ""
                if not conv_price or not stk:
                    return ctx.close
                stk_vt = to_vt_symbol(stk)
                cur = conn.execute(
                    "SELECT close FROM bar_1d WHERE symbol=%s ORDER BY ts DESC LIMIT 1", (stk_vt,))
                r2 = cur.fetchone()
                if not r2 or not r2[0]:
                    return ctx.close
                parity = 100.0 * float(r2[0]) / conv_price   # 转股价值
                premium = (ctx.close / parity - 1) * 100 if parity > 0 else 0.0
                return ctx.close + premium
        except Exception:
            return ctx.close


@register_factor("funding_rate", category="crypto", needs_history=0,
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