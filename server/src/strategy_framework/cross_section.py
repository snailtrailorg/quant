"""横截面 DSL（批 164c 期 3/3）——校验通道+求值器，独立于时序链（factor.py 零改动）。

设计真源：flow/任务/批164c-横截面DSL.md v2（双盲处置）。
层位：strategy_framework（层2）import data_platform.screen（层1）——合法向（factor.py:96 先例）。
词表单源：变量=注册表品类字段名；函数=_XSEC_FUNCS（校验与求值 env 同引——A-P1-3）。
"""
from __future__ import annotations

import ast

from ..data_platform.screen import FIELDS, fields_for


def _v_placeholder(x):
    raise RuntimeError("横截面函数须预计算列（两遍求值）")


_XSEC_FUNCS = {
    "rank": _v_placeholder, "zscore": _v_placeholder, "percentile": _v_placeholder,
    "abs": abs, "max": max, "min": min, "round": round,
}
_XSEC_NUM_FUNCS = {"abs", "max", "min", "round"}
_XSEC_VECTOR_FUNCS = {"rank", "zscore", "percentile"}
_MAX_CALLS = 10


class _Validator(ast.NodeVisitor):
    def __init__(self, fields):
        self.fields = fields
        self.calls = 0
        self.err = None

    def _fail(self, code, msg):
        if self.err is None:
            self.err = f"{code}: {msg}"

    def visit_Name(self, node):
        ok = node.id in self.fields or node.id in _XSEC_NUM_FUNCS \
            or node.id in _XSEC_VECTOR_FUNCS or node.id in ("true", "false")
        if not ok:
            self._fail("XSEC_UNKNOWN_FIELD", str(node.id))
        self.generic_visit(node)

    def visit_Call(self, node):
        self.calls += 1
        if self.calls > _MAX_CALLS:
            self._fail("XSEC_TOO_MANY", f"函数调用超 {_MAX_CALLS}")
            return
        fname = node.func.id if isinstance(node.func, ast.Name) else None
        if fname in _XSEC_VECTOR_FUNCS:
            for sub in ast.walk(node.args[0] if node.args else ast.Constant(value=0)):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                        and sub.func.id in _XSEC_VECTOR_FUNCS:
                    self._fail("XSEC_NESTED", f"{fname} 内禁嵌横截面函数")
                    return
        elif fname not in _XSEC_NUM_FUNCS:
            self._fail("XSEC_FORBIDDEN_FUNC", str(fname))
        if node.keywords:
            self._fail("XSEC_SYNTAX", "不支持 kwargs")
        self.generic_visit(node)

    def generic_visit(self, node):
        if self.err:
            return
        if isinstance(node, (ast.Import, ast.Attribute, ast.Subscript, ast.Lambda,
                             ast.ListComp, ast.DictComp, ast.Dict, ast.Tuple, ast.Set,
                             ast.JoinedStr, ast.Await, ast.Yield)):
            self._fail("XSEC_SYNTAX", f"禁节点 {type(node).__name__}")
            return
        super().generic_visit(node)


def validate_cross_section_expr(expr, category):
    if len(expr) > 500:
        raise ValueError("XSEC_SYNTAX: 表达式过长")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"XSEC_SYNTAX: {e}")
    v = _Validator(set(fields_for(category)))
    v.visit(tree)
    if v.err:
        raise ValueError(v.err)


def _eval_scalar(node, env):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in _XSEC_NUM_FUNCS:
            return _XSEC_FUNCS[node.id]
        raise NameError(node.id)
    if isinstance(node, ast.BinOp):
        import operator as op_
        ops = {ast.Add: op_.add, ast.Sub: op_.sub, ast.Mult: op_.mul,
               ast.Div: op_.truediv, ast.Pow: op_.pow, ast.Mod: op_.mod}
        fn = ops.get(type(node.op))
        if fn is None:
            raise TypeError(type(node.op).__name__)
        return fn(_eval_scalar(node.left, env), _eval_scalar(node.right, env))
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            return -_eval_scalar(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not _eval_scalar(node.operand, env)
        raise TypeError(type(node.op).__name__)
    if isinstance(node, ast.Compare):
        import operator as op_
        cmps = {ast.Lt: op_.lt, ast.LtE: op_.le, ast.Gt: op_.gt,
                ast.GtE: op_.ge, ast.Eq: op_.eq, ast.NotEq: op_.ne}
        left = _eval_scalar(node.left, env)
        for opn, rnode in zip(node.ops, node.comparators):
            fn = cmps.get(type(opn))
            if fn is None:
                raise TypeError(type(opn).__name__)
            right = _eval_scalar(rnode, env)
            if not fn(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        vals = [_eval_scalar(v, env) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Call):
        fname = node.func.id
        if fname in _XSEC_VECTOR_FUNCS:
            return env.get(f"__xsec_{id(node)}")
        if fname in _XSEC_NUM_FUNCS:
            return _XSEC_FUNCS[fname](*[_eval_scalar(a, env) for a in node.args])
        raise NameError(fname)
    raise TypeError(type(node).__name__)


def run_cross_section(category, filter_expr, rank_expr, limit=100):
    from ..data_platform.db import get_conn
    from ..data_platform.screen import build_base_from

    if filter_expr:
        validate_cross_section_expr(filter_expr, category)
    if rank_expr:
        validate_cross_section_expr(rank_expr, category)

    # 单次 parse（同一棵树——xsec 列回填按 id(node) 键，多棵树的 id 对不上）
    ftree_all = ast.parse(filter_expr, mode="eval") if filter_expr else None
    rtree_all = ast.parse(rank_expr, mode="eval") if rank_expr else None
    flds = fields_for(category)
    used = set()
    for tree in (ftree_all, rtree_all):
        if tree is None:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and n.id in flds:
                used.add(n.id)

    base = build_base_from(category, sorted(used) or None)
    with get_conn() as conn:
        cur = conn.execute(base)
        cols = [d[0] for d in cur.description]
        universe = [dict(zip(cols, r)) for r in cur.fetchall()]

    def _vectorize(fname, argnode):
        vals, rows_idx = [], []
        for i, row in enumerate(universe):
            try:
                v = _eval_scalar(argnode, dict(row))
                if v is None or isinstance(v, bool):
                    continue
                vals.append(float(v))
                rows_idx.append(i)
            except (TypeError, NameError, ZeroDivisionError):
                continue
        if not vals:
            return {}
        import statistics
        s = sorted(vals)
        m = len(s)
        out = {}
        if fname == "rank":
            for j, i in enumerate(rows_idx):
                out[i] = (s.index(vals[j]) + 1) / m
        elif fname == "zscore":
            mu = statistics.mean(vals)
            sd = statistics.pstdev(vals) or 1.0
            out = {i: (v - mu) / sd for i, v in zip(rows_idx, vals)}
        else:
            for j, i in enumerate(rows_idx):
                out[i] = 100.0 * s.index(vals[j]) / max(m - 1, 1)
        return out

    xsec_cols = {}
    for tree in (ftree_all, rtree_all):
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in _XSEC_VECTOR_FUNCS:
                xsec_cols[id(node)] = _vectorize(node.func.id, node.args[0])

    ftree = ftree_all.body if ftree_all is not None else None
    rtree = rtree_all.body if rtree_all is not None else None
    results = []
    for i, row in enumerate(universe):
        env = dict(row)
        for nid, col in xsec_cols.items():
            env[f"__xsec_{nid}"] = col.get(i)
        try:
            if ftree is not None:
                ok = _eval_scalar(ftree, env)
                if isinstance(ok, float):
                    ok = ok != 0.0
                if not ok:
                    continue
            out = dict(row)
            if rtree is not None:
                rv = _eval_scalar(rtree, env)
                out["__rank__"] = float(rv) if rv is not None and not isinstance(rv, bool) else -1e18
            results.append(out)
        except (TypeError, NameError, ZeroDivisionError, ValueError):
            continue

    if rtree is not None:
        results.sort(key=lambda r: r.get("__rank__", -1e18), reverse=True)
    else:
        results.sort(key=lambda r: float(r.get("total_mv") or 0), reverse=True)
    return results[: int(limit)]
