#!/usr/bin/env python3
"""clean-sync 演练工具（批 94）——「快照 → 清空 → 全量重同步 → 挖洞 → 补洞 → 核验」。

为什么需要它：仓内**没有任何「清空/重建」路径**（`grep TRUNCATE` 只命中 vendored 库），
而「从空库重建」是验证同步引擎最硬的手段。本工具把该流程固化为可重复、可审计的动作。

清空集（`CLEAR_TABLES`）= **只含「同步引擎能重建」的市场数据表**——判据两条：
  ① 该表由某个 `sync_config.pg_table` 或 `pool_data` 负责重建；
  ② 该表无外键（已实测：市场数据表零 FK，FK 只存在于业务表之间）。
**不碰**：身份/业务层（security_master / security_state / users / pools / …）、
所有日志（system_log / sync_log / audit_log / tasks …）——清日志=自毁取证能力。

安全门禁（三道，缺一不执行）：
  1. 目标库必须是本机（host ∈ 127.0.0.1 / localhost）——生产 120.24.235.98 无任意 SQL 通道，
     天然挡死；本门防的是「在本机把不该动的库当沙箱」。
  2. 破坏性动作（clear / poke-*）缺 `--commit` 一律只打印计划（dry-run 默认）。
  3. clear / poke-* 额外要 `--yes-nonprod`。

用法（cwd = server/，让 .env 被加载）：
  python scripts/rehearse_clean_sync.py snapshot
  python scripts/rehearse_clean_sync.py backup
  python scripts/rehearse_clean_sync.py poke-cursor --sync-id margin_detail_sync --to 20260831 --commit --yes-nonprod
  python scripts/rehearse_clean_sync.py repair --sync-id margin_detail_sync
  python scripts/rehearse_clean_sync.py poke-delete --table margin_detail --from 20260928 --to 20260928 --commit --yes-nonprod
  python scripts/rehearse_clean_sync.py clear --from 20260101 --commit --yes-nonprod
  python scripts/rehearse_clean_sync.py resync
  python scripts/rehearse_clean_sync.py verify --snapshot /home/bernard/rehearse/<file>.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRV = os.path.join(_HERE, "..")
if os.path.isdir(os.path.join(_SRV, "src")):
    sys.path.insert(0, _SRV)

OUT_DIR = os.environ.get("REHEARSE_OUT", "/home/bernard/rehearse")

# ── 清空集：只含「同步引擎能重建」的市场数据表（无 FK，已实测） ──
CLEAR_TABLES: list[str] = [
    # 参考/日历层（必须最先重建——b92 的 _data_ready_end_date 依赖 trade_cal）
    "trade_cal", "static_symbols", "asset_static_info",
    "cb_basic_info", "convertible_terms", "namechange", "concept",
    # 行情层
    "bar_1d", "bar_1min", "bar_5min", "bar_index", "daily_basic",
    # 一档日频
    "stk_limit", "moneyflow", "margin_detail", "top_list",
    "block_trade", "cyq_perf", "forecast",
    # 二档池数据
    "income", "balancesheet", "cashflow", "fina_indicator",
    "cyq_chips", "top10_holders", "dividend", "pledge_stat",
    "share_float", "stk_holdernumber",
]

# ── 重建顺序（依赖优先：日历 → 身份 → 行情 → 一档 → 二档） ──
RESYNC_ORDER: list[str] = [
    "trade_cal",
    "astock_list", "etf_list", "cb_basic", "static_symbols",
    "namechange_sync", "concept_sync", "convertible_terms",
    "astock_daily", "etf_daily", "cb_daily", "index_daily", "astock_basic",
    "stk_limit_sync", "moneyflow_sync", "margin_detail_sync", "top_list_sync",
    "block_trade_sync", "cyq_perf_sync", "forecast_sync",
    "pool_data",
]

# 日期列（用于 snapshot 的覆盖范围 + poke-delete 的范围过滤）
DATE_COL: dict[str, str] = {
    "bar_1d": "ts", "bar_1min": "ts", "bar_5min": "ts", "bar_index": "ts",
    "daily_basic": "trade_date", "stk_limit": "trade_date", "moneyflow": "trade_date",
    "margin_detail": "trade_date", "top_list": "trade_date", "block_trade": "trade_date",
    "cyq_perf": "trade_date", "trade_cal": "cal_date", "forecast": "ann_date",
    "namechange": "ann_date", "income": "ann_date",
}

# 各 sync_id 的增量滞后（交易日）——与 engine._TIER1_LAG_TRADING_DAYS 同源
LAG_BY_SYNC_ID = {"margin_detail_sync": 1}

# 复刻式重建：表 → 依赖其最早日期作游标起点的 sync_id（增量型才有意义）
TABLE_TO_SYNC: dict[str, list[str]] = {
    "bar_1d": ["astock_daily", "etf_daily", "cb_daily"],
    "daily_basic": ["astock_basic"],
    "stk_limit": ["stk_limit_sync"],
    "moneyflow": ["moneyflow_sync"],
    "margin_detail": ["margin_detail_sync"],
    "top_list": ["top_list_sync"],
    "block_trade": ["block_trade_sync"],
    "cyq_perf": ["cyq_perf_sync"],
    "forecast": ["forecast_sync"],
}


def _norm_date(s):
    return datetime.strptime(str(s).replace("-", "")[:8], "%Y%m%d").date()


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


def _db():
    import psycopg

    from src.data_platform.db import get_conn_url
    url = os.environ.get("REHEARSE_DB_URL") or get_conn_url()
    return psycopg.connect(url, autocommit=True), url


def guard_local(url: str) -> None:
    """门禁 1：目标库必须是本机。"""
    host = url.split("@")[-1].split("/")[0].split(":")[0]
    if host not in ("127.0.0.1", "localhost", "::1", ""):
        sys.exit(f"✗ 门禁拒绝：目标库 host={host!r} 非本机。本工具只允许在本机库上演练。")
    log(f"门禁①通过：目标库 host={host or 'local'}")


def guard_commit(args) -> bool:
    """门禁 2/3：--commit + --yes-nonprod 齐备才真写。"""
    if not getattr(args, "commit", False):
        log("门禁②：未给 --commit → 只打印计划（dry-run）")
        return False
    if not getattr(args, "yes_nonprod", False):
        sys.exit("✗ 门禁③：破坏性动作须显式 --yes-nonprod（确认目标非生产）")
    return True


def _table_existing(conn, tables: list[str]) -> list[str]:
    cur = conn.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind='r' AND c.relname = ANY(%s)", (tables,))
    have = {r[0] for r in cur.fetchall()}
    return [t for t in tables if t in have]


def _date_expr(table: str) -> str | None:
    col = DATE_COL.get(table)
    if not col:
        return None
    if col == "ts":
        return "min((ts AT TIME ZONE 'Asia/Shanghai')::date), max((ts AT TIME ZONE 'Asia/Shanghai')::date)"
    return f"min({col}), max({col})"


def _counts(conn, tables: list[str]) -> dict:
    out = {}
    for t in tables:
        try:
            row = conn.execute(f"SELECT count(*) FROM {t}").fetchone()
            rec = {"rows": row[0]}
            de = _date_expr(t)
            if de:
                r2 = conn.execute(f"SELECT {de} FROM {t}").fetchone()
                rec["min"], rec["max"] = (str(r2[0]) if r2 and r2[0] else None,
                                          str(r2[1]) if r2 and r2[1] else None)
            out[t] = rec
        except Exception as e:  # noqa: BLE001
            out[t] = {"error": str(e)[:80]}
    return out


# ── 阶段：snapshot ──
def cmd_snapshot(args) -> None:
    conn, url = _db()
    guard_local(url)
    tables = _table_existing(conn, CLEAR_TABLES + ["security_master", "security_state"])
    snap = {"at": datetime.now().isoformat(timespec="seconds"), "host": url.split("@")[-1],
            "tables": _counts(conn, tables),
            "cursors": {}}
    cur = conn.execute("SELECT id, last_sync_date, last_status FROM sync_config ORDER BY id")
    for sid, lsd, st in cur.fetchall():
        snap["cursors"][sid] = {"last_sync_date": lsd, "last_status": st}
    cur = conn.execute("SELECT table_name, last_pull_date FROM pool_data_cursor")
    for t, d in cur.fetchall():
        snap["cursors"].setdefault("pool_data_cursor", {})[t] = d
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-snapshot.json")
    with open(path, "w") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    log(f"快照已写：{path}")
    log(f"{'表':<20}{'行数':>12}  {'min':<12}{'max':<12}")
    for t, rec in sorted(snap["tables"].items(), key=lambda kv: -(kv[1].get("rows") or 0)):
        log(f"{t:<20}{rec.get('rows', '-'):>12}  {str(rec.get('min') or ''):<12}{str(rec.get('max') or ''):<12}")


# ── 阶段：backup（pg_dump 兜底） ──
def cmd_backup(args) -> None:
    conn, url = _db()
    guard_local(url)
    tables = _table_existing(conn, CLEAR_TABLES)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-clearset.dump")
    cmd = ["pg_dump", "-Fc", "-f", path]
    for t in tables:
        cmd += ["-t", f"public.{t}"]
    cmd.append(url)
    log(f"pg_dump {len(tables)} 张表 → {path}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"✗ pg_dump 失败：{r.stderr.strip()[:300]}")
        sys.exit(1)
    log(f"✓ 备份完成：{path}（{os.path.getsize(path) / 1e6:.0f} MB）")


# ── 阶段：clear ──
def cmd_clear(args) -> None:
    conn, url = _db()
    guard_local(url)
    tables = _table_existing(conn, CLEAR_TABLES)
    log(f"待清空 {len(tables)} 张表：" + ", ".join(tables))
    if not guard_commit(args):
        log("--- DRY-RUN 结束（加 --commit --yes-nonprod 真清空）---")
        return
    if args.from_date == "auto":
        # 复刻式：在 TRUNCATE **之前**读各表原最早日期（清完就没得读了），
        # 增量型 sync_id 的游标 = 原最早日期 - 1 天 ⇒ 重同步复刻出相同或更好的水位
        mins = {}
        for t in tables:
            de = _date_expr(t)
            if de:
                r = conn.execute(f"SELECT {de} FROM {t}").fetchone()
                if r and r[0]:
                    mins[t] = str(r[0])
        cursor_map: dict[str, str] = {}
        for t, syncs in TABLE_TO_SYNC.items():
            if t in mins:
                d = _norm_date(mins[t]) - timedelta(days=1)
                for s in syncs:
                    cursor_map[s] = d.strftime("%Y%m%d")
        log("复刻式游标复位（每表原最早日期 -1 天）："
            + ", ".join(f"{s}→{d}" for s, d in sorted(cursor_map.items())))
        fallback = "20260101"   # 全量重建型 handler（namechange/concept/清单类）不用游标，占位即可
    else:
        cursor_map = {}
        fallback = args.from_date
        log(f"统一起点：{args.from_date}")
    for t in tables:
        conn.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY")
    conn.execute("UPDATE sync_config SET last_sync_date=%s, last_sync_ts=NULL, last_status='idle' "
                 "WHERE id = ANY(%s)", (fallback, RESYNC_ORDER))
    for sid, d in cursor_map.items():
        conn.execute("UPDATE sync_config SET last_sync_date=%s, last_sync_ts=NULL, "
                     "last_status='idle' WHERE id=%s", (d, sid))
    conn.execute("DELETE FROM pool_data_cursor")
    log(f"✓ 已清空 {len(tables)} 张表；游标复位完成（fallback={fallback}）；pool_data_cursor 已清空")


# ── 阶段：resync ──
def cmd_resync(args) -> None:
    conn, url = _db()
    guard_local(url)
    from src.data_sync.engine import _data_ready_end_date, sync
    only = set(args.only.split(",")) if args.only else None
    order = [s for s in RESYNC_ORDER if not only or s in only]
    log(f"重同步 {len(order)} 个 sync_id（按依赖顺序）")
    report = []
    t_all = time.time()
    for sid in order:
        t0 = time.time()
        try:
            r = sync(sid)
        except Exception as e:  # noqa: BLE001
            r = {"status": "EXCEPTION", "error": f"{type(e).__name__}: {str(e)[:200]}"}
        dt = time.time() - t0
        rec = {"sync_id": sid, "elapsed_s": round(dt, 1), **{k: r.get(k) for k in
               ("status", "pulled", "saved", "expected_days", "actual_days", "error", "reason")}}
        report.append(rec)
        log(f"  {sid:<28} {str(r.get('status')):<10} pulled={r.get('pulled')} "
            f"saved={r.get('saved')} {dt:.0f}s {r.get('error') or r.get('reason') or ''}")
    log(f"重同步完成，总耗时 {time.time() - t_all:.0f}s（预测上界 _data_ready_end_date={_data_ready_end_date()}）")
    path = os.path.join(OUT_DIR, f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-resync.json")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log(f"报告：{path}")


# ── 阶段：poke-cursor（回退游标制造空洞，安全——不动任何数据行） ──
def cmd_poke_cursor(args) -> None:
    conn, url = _db()
    guard_local(url)
    log(f"回退游标：{args.sync_id}.last_sync_date → {args.to}")
    if not guard_commit(args):
        return
    n = conn.execute("UPDATE sync_config SET last_sync_date=%s, last_sync_ts=NULL, last_status='idle' "
                     "WHERE id=%s", (args.to, args.sync_id)).rowcount
    log(f"✓ 影响 {n} 行")


# ── 阶段：poke-delete（真删数据行制造空洞） ──
def cmd_poke_delete(args) -> None:
    conn, url = _db()
    guard_local(url)
    col = args.date_col or DATE_COL.get(args.table)
    if not col:
        sys.exit(f"✗ 表 {args.table} 无已知日期列，必须显式 --date-col")
    if col == "ts":
        where = "(ts AT TIME ZONE 'Asia/Shanghai')::date BETWEEN %s AND %s"
    else:
        where = f"{col} BETWEEN %s AND %s"
    n0 = conn.execute(f"SELECT count(*) FROM {args.table} WHERE {where}", (args.from_date, args.to)).fetchone()[0]
    log(f"待删：{args.table} WHERE {where} [{args.from_date} ~ {args.to}] = {n0} 行")
    if not guard_commit(args):
        return
    conn.execute(f"DELETE FROM {args.table} WHERE {where}", (args.from_date, args.to))
    log(f"✓ 已删 {n0} 行（空洞已制造）")


# ── 阶段：repair（对某 sync_id 触发同步并核验） ──
def cmd_repair(args) -> None:
    conn, url = _db()
    guard_local(url)
    from src.data_sync.engine import _data_ready_end_date, sync
    lag = LAG_BY_SYNC_ID.get(args.sync_id, 0)
    log(f"补洞：sync_id={args.sync_id}（lag={lag} → 预期上界 {_data_ready_end_date(lag)}）")
    t0 = time.time()
    r = sync(args.sync_id, backfill_from=args.backfill_from)
    log(f"结果：{json.dumps({k: r.get(k) for k in ('status','pulled','saved','expected_days','actual_days','error')}, ensure_ascii=False)}"
        f"  ({time.time() - t0:.0f}s)")
    tbl = conn.execute("SELECT pg_table FROM sync_config WHERE id=%s", (args.sync_id,)).fetchone()
    if tbl and tbl[0] in DATE_COL:
        t = tbl[0]
        de = _date_expr(t)
        row = conn.execute(f"SELECT count(*), {de} FROM {t}").fetchone()
        log(f"核验 {t}：rows={row[0]} 覆盖={row[1]} ~ {row[2]}")


# ── 阶段：verify（对比快照） ──
def cmd_verify(args) -> None:
    conn, url = _db()
    guard_local(url)
    with open(args.snapshot) as f:
        snap = json.load(f)
    now = _counts(conn, list(snap["tables"].keys()))
    log(f"{'表':<20}{'快照':>12}{'现在':>12}   判定")
    bad = 0
    for t, before in snap["tables"].items():
        b, a = before.get("rows"), now.get(t, {}).get("rows")
        verdict = "✓" if (a is not None and b is not None and a >= b) else "✗ 缺"
        if verdict != "✓":
            bad += 1
        log(f"{t:<20}{b if b is not None else '-':>12}{a if a is not None else '-':>12}   {verdict}")
    log(f"—— {len(snap['tables']) - bad}/{len(snap['tables'])} 张表已恢复到 ≥ 快照水位；{bad} 张仍缺 ——")


def main() -> None:
    ap = argparse.ArgumentParser(description="clean-sync 演练（批 94）")
    sub = ap.add_subparsers(dest="stage", required=True)

    sub.add_parser("snapshot").set_defaults(func=cmd_snapshot)
    sub.add_parser("backup").set_defaults(func=cmd_backup)

    p = sub.add_parser("clear")
    p.add_argument("--from", dest="from_date", default="auto",
                   help="游标复位起点：auto=按各表原最早日期（复刻式，默认）；或 YYYYMMDD")
    p.add_argument("--commit", action="store_true")
    p.add_argument("--yes-nonprod", action="store_true")
    p.set_defaults(func=cmd_clear)

    p = sub.add_parser("resync")
    p.add_argument("--only", default=None, help="逗号分隔 sync_id 子集")
    p.set_defaults(func=cmd_resync)

    p = sub.add_parser("poke-cursor")
    p.add_argument("--sync-id", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--commit", action="store_true")
    p.add_argument("--yes-nonprod", action="store_true")
    p.set_defaults(func=cmd_poke_cursor)

    p = sub.add_parser("poke-delete")
    p.add_argument("--table", required=True)
    p.add_argument("--date-col", default=None)
    p.add_argument("--from", dest="from_date", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--commit", action="store_true")
    p.add_argument("--yes-nonprod", action="store_true")
    p.set_defaults(func=cmd_poke_delete)

    p = sub.add_parser("repair")
    p.add_argument("--sync-id", required=True)
    p.add_argument("--backfill-from", default=None)
    p.set_defaults(func=cmd_repair)

    p = sub.add_parser("verify")
    p.add_argument("--snapshot", required=True)
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
