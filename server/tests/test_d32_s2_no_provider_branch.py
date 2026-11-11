"""S2 守门：端口/注册表/领域层不得出现 provider 或 category 分支（D32 §十四）。

只扫「新目录」——存量应用层的合法字面量（注册表、日志、权限映射）不在此列。
口径：
- 禁 `Compare`（`==`/`!=`/`in`）任一侧为 provider/category 字面量，含元组/列表/集合展开
- 禁 `match` 的 `case "xtp"`（与 if/elif 等价，不能留正门）
- 允许：dict/列表字面量作注册表、函数默认值

词表从 `quant_common/markets.py` 派生（单一真源），不在这里手抄——新增 provider 自动纳入。

防空转：目标目录必须存在且各有 ≥1 个 .py；否则测试红（目录改名/搬迁后不能静默变绿）。
注意：TARGETS 是显式列表，**新增目录必须手动加入**（这是有意的：不自动扫全仓，避免存量误伤）。
"""

from __future__ import annotations

import ast
import os

from src.quant_common.markets import ASSET_CATEGORIES, NON_DATA_PROVIDERS, PROVIDER_MARKET

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
TARGETS = ["ports", "registry", os.path.join("quant_common", "domain")]

# 词表单一真源：provider 来自 markets 两张映射（+ paper 虚拟通道），category 来自 ASSET_CATEGORIES
PROVIDER_LITERALS = set(PROVIDER_MARKET) | set(NON_DATA_PROVIDERS) | {"paper"}
CATEGORY_LITERALS = set(ASSET_CATEGORIES) | {"future", "option"}   # 后两个是 D32 扩展位
BRANCH_LITERALS = PROVIDER_LITERALS | CATEGORY_LITERALS


def _literals(node: ast.AST) -> set[str]:
    """取比较操作数里的字符串字面量（含元组/列表/集合展开，堵 `p in ("xtp",)`）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out: set[str] = set()
        for e in node.elts:
            out |= _literals(e)
        return out
    return set()


def find_branches() -> list[str]:
    hits: list[str] = []
    for target in TARGETS:
        root = os.path.join(SRC, target)
        if not os.path.isdir(root):
            raise AssertionError(f"扫描目标不存在：{root}（目录改名后本守门会静默变绿，故直接失败）")
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                rel = os.path.relpath(path, SRC).replace(os.sep, "/")
                tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Compare):
                        sides = [node.left, *node.comparators]
                        for s in sides:
                            for lit in sorted(_literals(s) & BRANCH_LITERALS):
                                hits.append(f"{rel}:{node.lineno} 比较字面量 {lit!r}")
                    elif isinstance(node, ast.Match):
                        for case in node.cases:
                            pat = case.pattern
                            if isinstance(pat, ast.MatchValue):
                                for lit in sorted(_literals(pat.value) & BRANCH_LITERALS):
                                    hits.append(f"{rel}:{case.pattern.lineno} match 分支 {lit!r}")
    return hits


def test_no_provider_or_category_branch():
    hits = find_branches()
    assert not hits, "端口/注册表/领域层出现 provider 或 category 分支：\n  " + "\n  ".join(hits)


def test_targets_are_not_empty():
    """防空转：每个目标目录至少有一个 .py。"""
    for target in TARGETS:
        root = os.path.join(SRC, target)
        pys = [f for _d, _s, fs in os.walk(root) for f in fs if f.endswith(".py")]
        assert pys, f"扫描目标为空：{target}"


def test_literals_expand_tuple():
    """堵 `p in ("xtp",)`：元组/列表/集合内的字面量必须被展开。"""
    expr = ast.parse('p in ("xtp", "emt")').body[0].value
    assert isinstance(expr, ast.Compare)
    assert _literals(expr.comparators[0]) == {"xtp", "emt"}
