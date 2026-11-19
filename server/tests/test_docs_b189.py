"""批189 文档断言门禁（M0 前置）。

把「文档里的代码断言」变成可计数项：键族/错误码/端点/符号/文件引用/禁用表述。
改契约不同步改断言 = 本测试红；台账需与文档同步（run: python3 scripts/verify_b189_docs.py --emit）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "verify_b189_docs", ROOT / "scripts" / "verify_b189_docs.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    return m


def test_docs_code_assertions_all_pass():
    rows = _mod().collect()
    bad = [(cat, key, why) for cat, key, why, ok in rows if not ok]
    assert not bad, f"文档断言未过 {len(bad)} 条：{bad}"


def test_ledger_in_sync():
    m = _mod()
    assert m.LEDGER.exists(), "缺 flow/审查/批189-断言台账.md（运行 --emit）"
    expected = m.render(m.collect())
    assert m.LEDGER.read_text(encoding="utf-8") == expected, \
        "台账与文档不同步：运行 python3 scripts/verify_b189_docs.py --emit"
