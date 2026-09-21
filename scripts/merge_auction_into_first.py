#!/usr/bin/env python3
"""批 58b：存量分钟竞价条并入首根（一次性迁移工具——本地 dev/彩排对账用）。

把 bar_1min/bar_5min 存量 dataset_version=1 的 09:30 竞价条并入 09:31 首根：
volume/amount 累加、open 取竞价条、high/low 取 max/min，09:31 条 dataset_version=2，删 09:30 条。
幂等（只处理 dataset_version=1 的行，二次跑无行可并）。

用法（在 server/ 目录，须能连目标库）：
    venv/bin/python ../scripts/merge_auction_into_first.py --dry-run
    venv/bin/python ../scripts/merge_auction_into_first.py --freq 1min

环境：
    QUANT_SERVER_DIR —— 覆盖 server 目录（默认本文件 ../server）；DB 连串沿用 db.py 读法。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SERVER_DIR = os.environ.get("QUANT_SERVER_DIR", str(Path(__file__).resolve().parent.parent / "server"))
sys.path.insert(0, SERVER_DIR)

from src.data_platform.db import get_conn  # noqa: E402

# 09:30 竞价条并入 09:31 首根（ts 是 UTC aware，转上海时区判 HH:MM）；只并 dataset_version=1 的旧行
_MERGE_SQL = """
UPDATE {table} f SET
    volume = f.volume + a.volume,
    amount = f.amount + a.amount,
    open = a.open,
    high = GREATEST(f.high, a.high),
    low = LEAST(f.low, a.low),
    dataset_version = 2
FROM {table} a
WHERE f.symbol = a.symbol
  AND (f.ts AT TIME ZONE 'Asia/Shanghai')::time = '09:31:00'
  AND (a.ts AT TIME ZONE 'Asia/Shanghai')::time = '09:30:00'
  AND (f.ts AT TIME ZONE 'Asia/Shanghai')::date = (a.ts AT TIME ZONE 'Asia/Shanghai')::date
  AND f.dataset_version = 1
  AND a.dataset_version = 1
"""

_DELETE_SQL = """
DELETE FROM {table}
WHERE (ts AT TIME ZONE 'Asia/Shanghai')::time = '09:30:00'
  AND dataset_version = 1
"""


def _count(table: str) -> tuple[int, int]:
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT count(*) FROM {table} WHERE dataset_version = 1 "
            f"AND (ts AT TIME ZONE 'Asia/Shanghai')::time IN ('09:30:00','09:31:00')")
        total = cur.fetchone()[0]
        cur = conn.execute(
            f"SELECT count(*) FROM {table} WHERE dataset_version = 1 "
            f"AND (ts AT TIME ZONE 'Asia/Shanghai')::time = '09:30:00'")
        auction = cur.fetchone()[0]
    return auction, total


def _merge_table(table: str, dry_run: bool) -> dict:
    auction, total = _count(table)
    if auction == 0:
        return {"table": table, "auction": 0, "merged": 0, "skipped": "无 09:30 旧行"}
    if dry_run:
        return {"table": table, "auction": auction, "merged": 0, "dry_run": True}
    with get_conn() as conn:
        cur = conn.execute(_MERGE_SQL.format(table=table))
        merged = cur.rowcount
        cur = conn.execute(_DELETE_SQL.format(table=table))
        deleted = cur.rowcount
        conn.commit()
    return {"table": table, "auction": auction, "merged": merged, "deleted": deleted}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--freq", default=None, help="只跑单表（1min/5min；缺省=两表全跑）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不执行")
    args = ap.parse_args()

    tables = {"1min": "bar_1min", "5min": "bar_5min"}
    if args.freq:
        if args.freq not in tables:
            print(f"✗ --freq 须 1min|5min（缺省两表全跑）: {args.freq}")
            return 2
        tables = {args.freq: tables[args.freq]}

    for _, tbl in tables.items():
        res = _merge_table(tbl, args.dry_run)
        if res.get("skipped"):
            print(f"⏭  {tbl}: {res['skipped']}")
        elif res.get("dry_run"):
            print(f"🔍 {tbl}: 将并入 {res['auction']} 条 09:30 竞价条（dry-run 未执行）")
        else:
            print(f"✅ {tbl}: 并入 {res['merged']} 条，删 {res['deleted']} 条 09:30 旧行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
