#!/usr/bin/env python3
"""批 58·M3 行为等价对照（真库两跑 diff——本地 dev 工具，不传服务器）。

对 bar 族 6 sync_id 逐个：flag off 跑 _HANDLERS → 快照窗口行 → 删窗口行 →
flag on 跑 _sync_via_kind → 快照 → 按 pk 排序全字段 diff。
比对只取 11 字段契约列（剔除 id 代理键——序列两跑不重置，含 id 必误报不等价）。

用法（在 server/ 目录，须能连 dev 库且 sync_kind_config 表已建）：
    venv/bin/python ../scripts/behavior_equiv_sync.py --sync-id cb_daily --start 20260901 --end 20260920
    venv/bin/python ../scripts/behavior_equiv_sync.py              # 默认 6 sync_id 全跑，窗口=近 5 交易日
    venv/bin/python ../scripts/behavior_equiv_sync.py --list       # 只列将对比的 sync_id 与表

环境：
    QUANT_DB_URL 沿用 db.py 的读法；QUANT_SERVER_DIR 覆盖 server 目录（默认本文件 ../server）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

SERVER_DIR = os.environ.get("QUANT_SERVER_DIR", str(Path(__file__).resolve().parent.parent / "server"))
sys.path.insert(0, SERVER_DIR)

from src.data_sync.engine import sync, _read_sync_kind  # noqa: E402
from src.data_platform.db import get_conn  # noqa: E402

BAR_SYNC_IDS = ["astock_daily", "etf_daily", "cb_daily", "index_daily",
                "astock_minute", "astock_minute_5min"]

# 三态表（逐日批）与区间表：diff 只需按 (symbol, ts) 排序全字段比，无需区分。
_WINDOW_FILTER = "(ts AT TIME ZONE 'Asia/Shanghai')::date >= %s AND (ts AT TIME ZONE 'Asia/Shanghai')::date <= %s"

# bar 表有 id BIGSERIAL 代理键（INSERT 不显式赋 id，序列两跑不重置）——比对必须剔除 id，
# 只取 11 字段契约列（BAR_COLUMNS 序）。
_BAR_COLS = "symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source"


def _safe_table(name: str) -> str:
    """表名白名单校验（防 pg_table 若未来可编辑成注入面）。"""
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError(f"非法表名: {name!r}")
    return name


# bar_daily 三品类共享 bar_1d 表——对照必须按品类 symbol 集合过滤（静态表 join），否则删窗口
# 会误删其他品类（如 cb 对照删掉股票/ETF 行 → 新旧快照错位）。品类真源=静态表 ts_code → vt_symbol。
_TB_TO_VT = "REPLACE(REPLACE(REPLACE(ts_code,'.SH','.SHSE'),'.SZ','.SZSE'),'.BJ','.BSE')"
_CATEGORY_TABLE = {
    ("bar_daily", "stock"): "asset_static_info",
    ("bar_daily", "etf"): "etf_basic_info",
    ("bar_daily", "convertible"): "cb_basic_info",
}


def _category_filter(kind: str, sub_kind: str | None) -> str:
    """品类 symbol 过滤 SQL（独立表 index/minute 无共享返回空串=不过滤）。"""
    tbl = _CATEGORY_TABLE.get((kind, sub_kind))
    if tbl is None:
        return ""
    return f"symbol IN (SELECT {_TB_TO_VT} FROM {tbl})"


def _set_routing(value: str | None) -> None:
    """写/清 system_config 键 sync_kind_routing（灰度白名单）。"""
    with get_conn() as conn:
        if value is None:
            conn.execute("DELETE FROM system_config WHERE key='sync_kind_routing'")
        else:
            conn.execute(
                "INSERT INTO system_config (key, value) VALUES ('sync_kind_routing', %s) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (value,))
        conn.commit()


def _snapshot(pg_table: str, start: str, end: str, cat_filter: str = "") -> list[tuple]:
    pg_table = _safe_table(pg_table)
    where = _WINDOW_FILTER + (f" AND {cat_filter}" if cat_filter else "")
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT {_BAR_COLS} FROM {pg_table} WHERE {where} ORDER BY symbol, ts",
            (start, end))
        return cur.fetchall()


def _delete_window(pg_table: str, start: str, end: str, cat_filter: str = "") -> int:
    pg_table = _safe_table(pg_table)
    where = _WINDOW_FILTER + (f" AND {cat_filter}" if cat_filter else "")
    with get_conn() as conn:
        cur = conn.execute(
            f"DELETE FROM {pg_table} WHERE {where}", (start, end))
        conn.commit()
        return cur.rowcount


def _check_one(sync_id: str, start: str, end: str, sleep_s: float = 0.0) -> dict:
    row = _read_sync_kind(sync_id)
    if not row:
        return {"sync_id": sync_id, "ok": False, "reason": "sync_kind_config 无归置行"}
    pg_table = row["pg_table"]
    cat_filter = _category_filter(row["kind"], row["sub_kind"])

    try:
        # 1. flag off → 旧 _HANDLERS 路径
        _set_routing(None)
        r_old = sync(sync_id, backfill_from=start)
        if r_old.get("status") not in ("success", "partial"):
            return {"sync_id": sync_id, "ok": False, "reason": f"旧路径失败: {r_old}"}
        old_snap = _snapshot(pg_table, start, end, cat_filter)

        # 2. 清窗口（仅本品类），sleep 隔离 adj_factor 限流（Tushare 短窗口 ~4-5 次/分钟，
        #    新旧路径连续跑共 2N 次会超配额→谁后跑谁失败，非代码逻辑差异），flag on → 新路径
        _delete_window(pg_table, start, end, cat_filter)
        if sleep_s:
            time.sleep(sleep_s)
        _set_routing(sync_id)
        r_new = sync(sync_id, backfill_from=start)
        new_snap = _snapshot(pg_table, start, end, cat_filter)
    finally:
        # 3. 收尾必清 flag（try/finally 防 _snapshot 抛异常残留 sync_kind_routing 污染后续 beat）
        _set_routing(None)

    if r_new.get("status") not in ("success", "partial"):
        return {"sync_id": sync_id, "ok": False, "reason": f"新路径失败: {r_new}"}

    if old_snap != new_snap:
        n = max(len(old_snap), len(new_snap))
        diffs = []
        for i, (a, b) in enumerate(zip(old_snap, new_snap)):
            if a != b:
                diffs.append((i, a, b))
                if len(diffs) >= 5:
                    break
        return {"sync_id": sync_id, "ok": False, "table": pg_table,
                "old_rows": len(old_snap), "new_rows": len(new_snap), "diffs": diffs}

    return {"sync_id": sync_id, "ok": True, "table": pg_table, "rows": len(new_snap)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sync-id", default=None, help="只查单个 sync_id（缺省=bar 族 6 个全跑）")
    ap.add_argument("--start", default=None, help="窗口起始 YYYYMMDD（缺省=近 5 交易日）")
    ap.add_argument("--end", default=None, help="窗口结束 YYYYMMDD（缺省=今日）")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="新旧路径之间 sleep 秒数（隔离 adj_factor 限流；bar_daily 建议 ≥60）")
    ap.add_argument("--list", action="store_true", help="只列将对比的 sync_id 与表")
    args = ap.parse_args()

    sync_ids = [args.sync_id] if args.sync_id else BAR_SYNC_IDS
    end = args.end or date.today().strftime("%Y%m%d")
    start = args.start or (date.today() - timedelta(days=10)).strftime("%Y%m%d")

    if args.list:
        for sid in sync_ids:
            row = _read_sync_kind(sid)
            print(f"{sid:20s} -> {row.get('pg_table') if row else '（无归置行）'}")
        return 0

    failed = 0
    for sid in sync_ids:
        res = _check_one(sid, start, end, sleep_s=args.sleep)
        if res["ok"]:
            print(f"✅ {sid}: 行为等价（{res.get('rows', 0)} 行全字段一致，表 {res.get('table')}）")
        else:
            failed += 1
            print(f"❌ {sid}: {res.get('reason')}")
            for d in res.get("diffs", []):
                print(f"   差异@{d[0]}: 旧={d[1]} 新={d[2]}")
    print(f"\n{'全部等价' if failed == 0 else f'{failed}/{len(sync_ids)} 不等价'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
