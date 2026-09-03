#!/usr/bin/env python3
"""wd-20 §1.3 · 回测成绩单历史回填（一次性运维工具）。

对 status='done' 且 summary_metrics IS NULL 的 backtest_runs，用与
src/scheduler/tasks.write_summary_metrics 相同的聚合规则回填
summary_metrics（total_return_pct/max_drawdown_pct/sharpe/win_rate/trade_count）。

用法（在 server/ 目录，须能连目标库）：
    venv/bin/python ../scripts/backfill_summary.py [--dry-run] [--batch 200]

    --dry-run  只打印将回填的 run 数与首条样例，不写库
    --batch    id 游标分批大小（默认 200）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import psycopg  # noqa: E402

DSN = "postgresql://quant@127.0.0.1:5432/quant"


def _summary(done_results: list[dict]) -> dict:
    def _avg(key):
        vals = [float(r.get(key) or 0) for r in done_results]
        return round(sum(vals) / len(vals), 4) if vals else None

    return {
        "total_return_pct": _avg("total_return_pct"),
        "max_drawdown_pct": _avg("max_drawdown_pct"),
        "sharpe": _avg("sharpe_ratio"),
        "win_rate": _avg("win_rate"),
        "trade_count": int(sum(int(r.get("total_trades") or 0) for r in done_results)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=200)
    args = ap.parse_args()

    conn = psycopg.connect(DSN)
    filled = 0
    cursor_id = 0
    with conn:
        with conn.cursor() as cur:
            while True:
                cur.execute(
                    "SELECT id FROM backtest_runs "
                    "WHERE status='done' AND summary_metrics IS NULL AND id > %s "
                    "ORDER BY id LIMIT %s", (cursor_id, args.batch))
                ids = [r[0] for r in cur.fetchall()]
                if not ids:
                    break
                for run_id in ids:
                    cursor_id = run_id
                    cur.execute(
                        "SELECT 1 FROM backtest_symbols WHERE run_id=%s AND status='done'",
                        (run_id,))
                    if not cur.fetchone():
                        continue
                    if args.dry_run:
                        if filled == 0:
                            cur.execute(
                                "SELECT result FROM backtest_symbols WHERE run_id=%s AND status='done'",
                                (run_id,))
                            results = [json.loads(r[0]) for r in cur.fetchall() if r[0]]
                            print(f"样例 run={run_id}: 结果 {len(results)} 符号")
                    else:
                        # 盲审B-P7：复用写入方单点（消灭手抄规则）+ 每批一 commit（18 号长事务规范）
                        from src.scheduler.tasks import write_summary_metrics
                        write_summary_metrics(conn, run_id)
                    filled += 1
                conn.commit()   # 每批一提交
    print(f"{'[dry-run] ' if args.dry_run else ''}回填完成: {filled} 条 done run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
