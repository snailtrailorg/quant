"""批189 schema 事实核对（防「把既有表当新建」）。

硬矛盾（新建表已存在 / 要加的列已存在）直接红；来源找不到只 WARN。
"""
from __future__ import annotations
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "verify_b189_schema_facts", ROOT / "scripts" / "verify_b189_schema_facts.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    return m


def test_no_new_table_already_exists():
    m = _mod()
    created, cols = m.scan()
    clash = [f"{t} 已存在于 {created[t]}" for t in m.NEW_TABLES if t in created]
    assert not clash, "把既有表当新建：" + "; ".join(clash)


def test_no_new_column_already_exists():
    m = _mod()
    _, cols = m.scan()
    clash = [f"{t}.{c} 已存在" for t, c in m.NEW_COLUMNS.items() if (t, c) in cols]
    assert not clash, "要新增的列已存在：" + "; ".join(clash)
