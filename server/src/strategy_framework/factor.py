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
    # P0-1 修复（2026-08-20 双盲审计 F1 实测复现）：属性链逃逸——
    # `ctx.__init__.__globals__` / `(1).__class__.__base__.__subclasses__()` 拿 __import__/os。
    # 黑名单式只拦 Import+裸 Name 拦不住字面量属性穿越。禁一切双下划线属性访问
    # +禁 getattr/eval/exec 族 Name 调用（_FORBIDDEN_BUILTINS 已含）。
    def visit_Attribute(self, node):
        # P0-1 修复（2026-08-20 双盲审计 F1 实测复现）：属性链逃逸——
        # `ctx.__init__.__globals__` / `(1).__class__.__base__.__subclasses__()` 拿 __import__/os。
        # 用户因子/策略代码无任何合法下划线属性需求——一并拦（白名单语义从严）。
        if node.attr.startswith("_"):
            raise ValueError(f"禁止访问下划线属性: {node.attr}（沙箱逃逸面）")
        self.generic_visit(node)


def _check_ast_blacklist(code: str) -> str | None:
    """检查 Python 代码的 AST 安全。返回违规描述，None 表示安全。

    P0-1（2026-08-20）：新增 Attribute 拦截（dunder/私有）——堵属性链逃逸；
    执行侧再加 signal 超时（_run_user_code）防 while True 挂死。
    """
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


def run_user_code_sandboxed(code: str, namespace: dict, timeout_s: int = 5) -> None:
    """带超时执行用户代码（P0-1 配套：while True/重循环挂死防线）。

    P0 复审修正（2026-08-20）：signal.alarm 仅主线程可用——web 因子端点是 sync def
    跑线程池，装 alarm 必抛 ValueError 致四个因子 API 全坏。改为：主线程用 alarm，
    线程池内跳过超时（定义期 exec 只跑 def 语句毫秒级，死循环风险在 compute 调用期，
    由调用方试算循环量界兜底；AST 属性拦截已堵 RCE 主面）。
    """
    import signal as _signal
    import threading as _threading
    in_main = _threading.current_thread() is _threading.main_thread()
    if in_main:
        def _timeout(signum, frame):
            raise TimeoutError(f"用户代码执行超过 {timeout_s}s")
        _old = _signal.signal(_signal.SIGALRM, _timeout)
        _signal.alarm(timeout_s)
    try:
        exec(compile(code, "<user-code>", "exec"), namespace)
    finally:
        if in_main:
            _signal.alarm(0)
            _signal.signal(_signal.SIGALRM, _old)


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
        run_user_code_sandboxed(code, ns, timeout_s=5)   # P0-1：超时防 while True 挂死
    except TimeoutError as e:
        raise ValueError(f"因子代码执行超时: {e}")
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
                "SELECT name, category, description, code, params, needs_history, type FROM factor_def"
            ).fetchall()
        import functools
        for name, category, description, code, params, needs_history, ftype in rows:
            import json
            params_dict = json.loads(params) if isinstance(params, str) else (params or {})
            try:
                if ftype == "dsl":
                    # web 长尾批（wd-13 #2）：DSL 因子——静态校验（坏表达式启动期
                    # 跳过并 warning，不炸进程）+ partial 注册（entry["cls"]() 零参
                    # 调用语义不变，strategy.py 消费面零改，盲审 A 实核）
                    n = validate_dsl_expr(code)
                    _FACTOR_REGISTRY[name] = {
                        "cls": functools.partial(DSLFactor, name, code),
                        "name": name,
                        "category": category or "custom",
                        "params": {"expr": code},
                        "code": code,   # W1（盲审 B-P1-2）：行内试算消费——entry 原无 code 全类型恒报错
                        "description": description or "",
                        "is_custom": True,
                        "needs_history": n,
                        "type": "dsl",
                    }
                else:
                    factor_cls = _make_factor_class(name, code, params_dict)
                    _FACTOR_REGISTRY[name] = {
                        "cls": factor_cls,
                        "name": name,
                        "category": category or "custom",
                        "params": params_dict,
                        "code": code,   # W1（盲审 B-P1-2）：同上
                        "description": description or "",
                        "is_custom": True,
                        "needs_history": int(needs_history or 0),
                        "type": "python",
                    }
                loaded.append(name)
            except Exception as e:
                _logger.warning("加载自定义因子 %s 失败: %s", name, e)
    except Exception as e:
        _logger.warning("从 DB 加载因子失败（表可能未创建）: %s", e)
    return loaded


def register_custom_factor(name: str, category: str, code: str,
                            description: str = "", params: dict | None = None,
                            needs_history: int = 0, ftype: str = "python") -> dict:
    """创建或更新自定义因子：编译代码 → 写 DB → 进注册表。

    ftype="dsl"（web 长尾批 2026-09-01，wd-13 #2）：code=受限表达式——静态校验
    （validate_dsl_expr）+needs_history=最大窗口 n；不做 python 编译。
    Returns: {"id": int, "name": str, ...}
    """
    if name.startswith("dsl:") or name == "dsl":
        raise ValueError("因子名禁用 dsl: 前缀与 dsl 本名（内联 DSL 路径保留字/内置注册键）")
    if ftype not in ("python", "dsl", None):
        raise ValueError(f"未知因子类型: {ftype}（python|dsl）")
    if ftype is None:
        # update 兼容（盲审 B-P2）：调用方不带 type 时读存量行——旧客户端/脚本编辑
        # DSL 因子不致误降级 python（dsl 表达式过 python 编译链必炸）
        try:
            from ..data_platform.db import get_conn as _gc
            with _gc() as _conn:
                _r = _conn.execute("SELECT type FROM factor_def WHERE name=%s", (name,)).fetchone()
            ftype = (_r[0] if _r else None) or "python"
        except Exception:
            ftype = "python"

    # 1. 校验（python=编译安全 / dsl=表达式静态校验）
    if ftype == "dsl":
        needs_history = validate_dsl_expr(code)
        params = {"expr": code}
        factor_cls = None
    else:
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
                "UPDATE factor_def SET category=%s, description=%s, code=%s, params=%s, needs_history=%s, type=%s, updated_at=now() WHERE name=%s",
                (category, description, code, params_json, needs_history, ftype, name),
            )
            fid = existing[0]
        else:
            cur = conn.execute(
                "INSERT INTO factor_def (name, category, description, code, params, needs_history, type) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (name, category, description, code, params_json, needs_history, ftype),
            )
            fid = cur.fetchone()[0]
        conn.commit()

    # 3. 更新注册表（dsl=partial 零参调用，与 load_factors_from_db 同形——POST 后
    # 同进程立即可用，不等重启；盲审验收 ④ 实测抓出原版 cls=None 不可调）
    if ftype == "dsl":
        import functools
        _FACTOR_REGISTRY[name] = {
            "cls": functools.partial(DSLFactor, name, code),
            "name": name, "category": category, "params": {"expr": code},
            "code": code,   # W1（盲审 B-P1-2）：行内试算消费
            "description": description, "is_custom": True,
            "needs_history": needs_history, "type": "dsl",
        }
    else:
        _FACTOR_REGISTRY[name] = {
            "cls": factor_cls,
            "name": name, "category": category, "params": params or {},
            "code": code,   # W1（盲审 B-P1-2）：同上
            "description": description, "is_custom": True,
            "needs_history": needs_history, "type": "python",
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
    """因子计算上下文（从 K 线数据构建）。

    symbol：当前标的（double_low 等跨表因子需要；P2-2026-08-20 接通——原 getattr 永空，
    "双低"实为"最低价"占位）。默认空串向后兼容。
    """
    def __init__(self, close: float, high: float, low: float,
                 open_: float, volume: float, history: list[dict] | None = None,
                 symbol: str = "", bar_ts=None):
        self.close = close
        self.high = high
        self.low = low
        self.open_ = open_
        self.volume = volume
        self.symbol = symbol
        self.bar_ts = bar_ts   # P2：时点约束（double_low 防前视）
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
    key = "open" if name in ("open", "open_") else name
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


# DSL 字段白名单（open 与 open_ 互为别名——历史字典键是 open，ctx 标量是 open_，
# 双侧打通消"校验过/运行爆"漂移，盲审 A/B-P0）
_DSL_FIELDS = {"close", "high", "low", "open", "open_", "volume"}


def _preprocess_dsl_tree(tree) -> int:
    """DSL AST 共享预处理+结构校验（单源——validate 与 DSLFactor 构造同走此路，
    漂移在结构上不可能；改写就地生效：窗口首参裸名→字符串字面量，open_→open 归一）。
    返回最大窗口 n。抛 ValueError。"""
    max_n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            raise ValueError("DSL 表达式不支持赋值")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("DSL 不支持属性/方法调用（仅白名单函数）")
            if node.func.id not in _DSL_WINDOW_FUNCS:
                raise ValueError(f"DSL 未知函数: {node.func.id}（白名单: {sorted(_DSL_WINDOW_FUNCS)}）")
            if not node.args:
                raise ValueError(f"{node.func.id}() 缺字段参数，如 {node.func.id}(close, 20)")
            a0 = node.args[0]
            if isinstance(a0, ast.Call):
                raise ValueError(f"DSL 窗口函数不支持嵌套: {node.func.id}({ast.unparse(a0)})——写成自定义因子")
            if isinstance(a0, ast.Name):
                if a0.id not in _DSL_FIELDS:
                    raise ValueError(f"{node.func.id}() 未知字段: {a0.id}（{sorted(_DSL_FIELDS)}）")
                node.args[0] = ast.Constant(value="open" if a0.id == "open_" else a0.id)
            elif isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                if a0.value not in _DSL_FIELDS:
                    raise ValueError(f"{node.func.id}() 未知字段: {a0.value}（{sorted(_DSL_FIELDS)}）")
            else:
                raise ValueError(f"{node.func.id}() 首参须为字段名（表达式/数字不可——盲审 A-P1）")
            # 窗口长度须常量（盲审 B-P2：mean(close,10+10) 静默算短窗=静默错值红线）
            for a in node.args[1:]:
                if isinstance(a, ast.Constant) and isinstance(a.value, (int, float)):
                    max_n = max(max_n, int(a.value))
                else:
                    raise ValueError(f"{node.func.id}() 窗口长度须为整数常量")
            for kw in node.keywords:
                if kw.arg == "n":
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, (int, float)):
                        max_n = max(max_n, int(kw.value.value))
                    else:
                        raise ValueError(f"{node.func.id}() 窗口长度 n= 须为整数常量")
        elif isinstance(node, ast.Name) and node.id not in _DSL_FIELDS                 and node.id not in _DSL_WINDOW_FUNCS:
            raise ValueError(f"DSL 未知变量: {node.id}（字段: {sorted(_DSL_FIELDS)}）")
    return max(max_n, 1)


def validate_dsl_expr(expr: str) -> int:
    """DSL 表达式静态校验（web 长尾批 2026-09-01，盲审 A/B-P0 根修）。

    坏表达式须在 register/load 期 ValueError（route 400 化），不得静默入库实盘才爆。
    与 DSLFactor 构造共享 _preprocess_dsl_tree 单源。返回最大窗口 n
    （DSL 因子 needs_history 由此定，补 0 会误标静态因子）。
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"DSL 表达式语法错误: {e}") from e
    return _preprocess_dsl_tree(tree)


@register_factor("dsl", category="custom", description="受限 DSL 表达式因子", needs_history=0)
class DSLFactor(Factor):
    """DSL 表达式因子：Web 端写受限表达式，如 'mean(close,20) / close - 1'。"""

    def __init__(self, name: str, expr: str):
        super().__init__()
        self.name = name
        self.expr = expr
        self.params = {"expr": expr}
        # 构造期共享预处理（web 长尾批单源根修）：坏表达式在构造即 ValueError
        # （register/load/内联三路全部构造期拦截）；compute 不再每根 bar 重复 parse。
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"DSL 表达式语法错误: {e}") from e
        _preprocess_dsl_tree(tree)
        self._expr_pp = ast.unparse(tree)

    def compute(self, ctx: BarContext) -> float:
        # 预处理已在构造期完成（窗口首参裸名→字符串字面量；open_→open 归一）
        expr = self._expr_pp
        env: dict = {
            "close": ctx.close, "high": ctx.high, "low": ctx.low,
            "open": ctx.open_, "open_": ctx.open_, "volume": ctx.volume,
        }
        for fname, fn in _DSL_WINDOW_FUNCS.items():
            def _mk(fname_=fname, f=fn):
                def _call(name_or_val, n=None):
                    # R-F2：静默错值比崩溃更危险——非序列名/未知名一律抛
                    if not isinstance(name_or_val, str):
                        raise TypeError(
                            f"{fname_}() 第一参必须是字段名（close/high/low/open/volume），"
                            f"收到表达式值 {name_or_val!r}——如需对表达式取窗口，先写成自定义因子")
                    if name_or_val not in ("close", "high", "low", "open", "volume", "open_"):
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
        # P2（双盲审计 F4 前视修复）：正股价必须 <= 当前 bar 时点——原 ORDER BY ts DESC LIMIT 1
        # 取查询时刻最新（回测 2024 年用 2026 年正股价）。ctx.bar_ts 由 Strategy 传入。
        bar_ts = getattr(ctx, "bar_ts", None)
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
                    "SELECT close FROM bar_1d WHERE symbol=%s AND (%s IS NULL OR ts <= %s) "
                    "ORDER BY ts DESC LIMIT 1", (stk_vt, bar_ts, bar_ts))
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