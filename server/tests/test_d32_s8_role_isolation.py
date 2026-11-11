"""批 172b · S8 角色隔离（结构面）：md_hub 不引用 ExecutionPort；strategy_runner 不引用 MarketDataPort。

突变复测（写完/改完必做）：
1. 在 server/src/md_hub/main.py 顶部加一行 `from src.ports import ExecutionPort`
2. `pytest tests/test_d32_s8_role_isolation.py -q` 必须红
3. 删除该行 → 复绿

扫描面：Import/ImportFrom + ast.Name；allowlist 见 fixtures/s8_role_allowlist.txt（空=零豁免）。
"""
from __future__ import annotations

import ast
import os

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
ALLOWLIST = os.path.join(os.path.dirname(__file__), "fixtures", "s8_role_allowlist.txt")

# 目录 → 该目录禁止出现的端口符号
FORBIDDEN = {
    "md_hub": "ExecutionPort",
    "strategy_runner": "MarketDataPort",
}


def _load_allowlist() -> set[str]:
    out: set[str] = set()
    if not os.path.exists(ALLOWLIST):
        return out
    with open(ALLOWLIST, encoding="utf-8") as f:
        for ln in f:
            s = ln.strip()
            if s and not s.startswith("#"):
                out.add(s)
    return out


def _hits(rel_root: str, symbol: str) -> set[str]:
    root = os.path.join(SRC, rel_root)
    hits: set[str] = set()
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, SRC).replace(os.sep, "/")
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == symbol:
                            hits.add(f"{rel}:{node.lineno}:{symbol}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == symbol or alias.name.endswith("." + symbol):
                            hits.add(f"{rel}:{node.lineno}:{symbol}")
                elif isinstance(node, ast.Name) and node.id == symbol:
                    hits.add(f"{rel}:{node.lineno}:{symbol}")
                elif isinstance(node, ast.Attribute) and node.attr == symbol:
                    hits.add(f"{rel}:{node.lineno}:{symbol}")
    return hits


def test_scan_target_non_empty():
    for rel_root in FORBIDDEN:
        assert any(fn.endswith(".py") for _d, _s, fs in os.walk(os.path.join(SRC, rel_root)) for fn in fs)


def test_role_isolation():
    allowed = _load_allowlist()
    bad: list[str] = []
    for rel_root, symbol in FORBIDDEN.items():
        bad.extend(sorted(_hits(rel_root, symbol) - allowed))
    assert not bad, "S8 角色隔离破坏：\n  " + "\n  ".join(bad)
