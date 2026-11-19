#!/usr/bin/env python3
"""批189 schema 事实核对（防「把既有表当新建」）。

来源：server/migrations/versions/*.py（create_table / add_column 正则）。
硬失败只在两类矛盾：①NEW_TABLES 已存在于迁移；②声称要新增的列已存在。
其余（找不到来源）打 WARN，供人工确认，不阻断。
"""
from __future__ import annotations
import re, sys, glob, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "server/migrations/versions"

NEW_TABLES = {"market_interface", "trade_interface", "td_md_map"}
NEW_COLUMNS = {
    "live_task": "account_code", "position_snapshot": "account_code",
    "position_refresh": "account_code", "account_snapshot": "account_code",
    "order_log": "account_code", "trade_log": "account_code",
}
REFERENCED = ["data_source", "provider_session_slot", "freeze_event",
              "account_permission", "data_source_usage", "trading_account",
              "live_task", "position_snapshot", "position_refresh",
              "account_snapshot", "order_log", "trade_log"]

# M0 §2.3 声明的引用表（身份列改造对象）——扫描出的写读点必须落在此集合内
DECLARED = {"live_task", "position_snapshot", "position_refresh", "account_snapshot",
            "order_log", "trade_log", "account_permission", "data_source_usage",
            "freeze_event", "trading_account", "provider_session_slot", "data_source"}


def scan():
    created, cols = {}, {}
    for f in sorted(MIG.glob("*.py")):
        s = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"create_table\(\s*['\"]([a-z_0-9]+)['\"]", s):
            created.setdefault(m.group(1), f.name)
        for m in re.finditer(r"add_column\(\s*['\"]([a-z_0-9]+)['\"]\s*,\s*(?:sa\.)?Column\(\s*['\"]([a-z_0-9]+)['\"]", s):
            cols.setdefault((m.group(1), m.group(2)), f.name)
        # rename 也是表来源（0116 前身/0104 venue_permission→account_permission）
        for m in re.finditer(r"ALTER TABLE\s+([a-z_0-9]+)\s+RENAME TO\s+([a-z_0-9]+)", s, re.I):
            created.setdefault(m.group(2), f.name + f" (renamed from {m.group(1)})")
    return created, cols


def scan_account_id_tables(created: dict) -> set:
    """扫 SQL 中与 account_id 同现的表名（只保留迁移中真实存在的表）。"""
    pat = re.compile(r"(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_][a-z_0-9]*)", re.I)
    used = set()
    for f in (ROOT / "server/src").rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "account_id" not in line:
                continue
            used |= {t for t in pat.findall(line) if t in created}
    return used


def main() -> int:
    created, cols = scan()
    fail, warn = [], []
    for t in sorted(NEW_TABLES):
        if t in created:
            fail.append(f"NEW_TABLES 中的 {t} 已在 {created[t]} 创建（不能再 CREATE）")
    for t, c in sorted(NEW_COLUMNS.items()):
        if (t, c) in cols:
            fail.append(f"要新增的 {t}.{c} 已存在于 {cols[(t, c)]}")
        if t not in created:
            warn.append(f"{t} 未在迁移中找到 create_table（确认是否由 SQL/其他方式创建）")
    for t in REFERENCED:
        if t not in created:
            warn.append(f"{t} 未在迁移中找到 create_table（需人工确认来源）")
    used = scan_account_id_tables(created)
    missing = sorted(used - DECLARED)
    if missing:
        fail.append(f"这些表有 account_id SQL 读写但未声明为改造对象：{missing}")
    print(f"迁移中建表 {len(created)} 张；硬矛盾 {len(fail)}，待确认 {len(warn)}")
    for x in fail: print("FAIL", x)
    for x in warn: print("WARN", x)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
