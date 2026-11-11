"""S1 守门：`providers/` 之外零 vnpy 依赖（D32 §十四）。

扫描面比 `test_layering` 宽：
- 递归子包（避免 `os.listdir` 非递归盲区）
- 直接 import / `__import__` / `importlib.import_module`
- PEP562 re-export 的传递可达：由被指向文件自身的直接 import 覆盖（定位会指向真实文件）

迁移期机制：`tests/fixtures/vnpy_import_baseline.txt` 冻结现有 `file:line`，
**只减不增**。P3/P4 每迁一个 provider，从基线删对应行；P5 完成后文件应为空。

守门自检：把任一 `import vnpy` 改成 `__import__("vnpy...")`，本测试必须红。
"""

from __future__ import annotations

import ast
import os

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
BASELINE = os.path.join(os.path.dirname(__file__), "fixtures", "vnpy_import_baseline.txt")
ALLOWED_PREFIX = os.path.join(SRC, "providers") + os.sep


def _rel(path: str) -> str:
    return os.path.relpath(path, SRC).replace(os.sep, "/")


def _is_vnpy(name: str | None) -> bool:
    """`vnpy` / `vnpy.*` / `vnpy_*`（后者是本体：vnpy_xtp 等，最常被搬的包）。"""
    if not name:
        return False
    root = name.split(".")[0]
    return root == "vnpy" or root.startswith("vnpy_")


def _dynamic_vnpy(node: ast.AST) -> bool:
    """`__import__("vnpy...")` / `importlib.import_module("vnpy...")`。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    if name not in {"__import__", "import_module"}:
        return False
    return any(
        isinstance(a, ast.Constant) and isinstance(a.value, str) and _is_vnpy(a.value)
        for a in node.args[:1]
    ) or any(
        isinstance(kw.value, ast.Constant)
        and isinstance(kw.value.value, str)
        and _is_vnpy(kw.value.value)
        for kw in node.keywords
        if kw.arg == "name"
    )


def find_vnpy_imports() -> set[str]:
    hits: set[str] = set()
    for root, _dirs, files in os.walk(SRC):
        if root.startswith(ALLOWED_PREFIX):
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(_is_vnpy(a.name) for a in node.names):
                        hits.add(f"{_rel(path)}:{node.lineno}:{node.col_offset}")
                elif isinstance(node, ast.ImportFrom):
                    if _is_vnpy(node.module):
                        hits.add(f"{_rel(path)}:{node.lineno}:{node.col_offset}")
                elif _dynamic_vnpy(node):
                    hits.add(f"{_rel(path)}:{node.lineno}:{node.col_offset}")
    return hits


def load_baseline() -> tuple[set[str], int]:
    """返回 (条目集, 声明条数)。声明条数是独立不变量：重刷基线必须显式改 count。"""
    if not os.path.exists(BASELINE):
        raise AssertionError(
            f"基线文件缺失：{BASELINE}\n"
            "不得自动生成（会变成永不红的守门）。请人工核对后提交。"
        )
    entries: set[str] = set()
    declared: int | None = None
    with open(BASELINE, encoding="utf-8") as f:
        for ln in f:
            s = ln.strip()
            if not s:
                continue
            if s.startswith("# count:"):
                declared = int(s.split(":", 1)[1].strip())
                continue
            if s.startswith("#"):
                continue
            entries.add(s)
    assert declared is not None, "基线缺少 `# count: N` 行——重刷时必须显式声明条数"
    return entries, declared


def test_baseline_declares_its_count():
    entries, declared = load_baseline()
    assert len(entries) == declared, (
        f"基线条数 {len(entries)} 与声明的 {declared} 不一致——重刷基线必须同时改 `# count`"
    )


def test_no_new_vnpy_imports():
    """实际集合必须 ⊆ 基线，且**总条数不得超过声明上限**（堵「按行重刷洗白」）。"""
    actual = find_vnpy_imports()
    baseline, declared = load_baseline()
    added = sorted(actual - baseline)
    assert not added, (
        "providers/ 外出现新的 vnpy 依赖（迁移期只减不增）：\n  "
        + "\n  ".join(added)
        + "\n如确属迁移需要，先改 D32 §12.3 并双盲审，不得直接往基线加行。"
    )
    assert len(actual) <= declared, (
        f"实际依赖数 {len(actual)} 超过基线上限 {declared}。"
        "迁移期该数字只能下降；若确需上升，先改 D32 §12.3 并双盲审。"
    )


def test_baseline_entries_still_exist():
    """基线不得留幽灵行（已迁走的条目必须删掉，否则「只减不增」失去意义）。"""
    actual = find_vnpy_imports()
    baseline, _ = load_baseline()
    stale = sorted(baseline - actual)
    assert not stale, "基线中以下条目已不存在，请从 baseline 删除：\n  " + "\n  ".join(stale)


def test_scanner_sees_a_known_import():
    """守门自检：扫描器必须能看见一个已知的 vnpy import（防扫描面静默失效）。"""
    actual = find_vnpy_imports()
    baseline, _ = load_baseline()
    if not baseline:
        return          # 迁移完成（基线为空），无对照物
    assert actual, "扫描结果为空——扫描路径或 AST 逻辑已失效"
    assert any("strategy_framework/adapters.py" in h for h in actual), (
        "扫描器未命中已知文件 strategy_framework/adapters.py，扫描面失效"
    )
