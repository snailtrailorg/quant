"""数据同步引擎 -- 通用增量/全量同步，按 sync_config.id 调度。

支持8种数据类型：astock_daily / astock_basic / astock_list /
cb_daily / cb_basic / etf_daily / etf_list / trade_cal

回补：sync(sync_id, backfill_from=YYYYMMDD) 回补历史缺口，不推进 last_sync_date 游标。
完整性：按日拉取的同步用 trade_cal 校验预期交易日 vs 实际成功日，缺口暴露在 sync_log。
"""

from __future__ import annotations
from src.data_platform.db import get_conn
import os
import time
from datetime import date, timedelta
from typing import Any, Callable
import psycopg
import pandas as pd
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("data_sync")

_sync_log_table_created = False


def _get_pro():
    """从 data_source_config DB 读 Tushare（DB 优先，.env fallback）。"""
    from src.data_platform.data_source import get_data_source
    ds = get_data_source("tushare")
    if ds:
        ds.record_usage(provider="tushare", api_name="get_pro")
        return ds.get_client()
    import tushare as ts
    return ts.pro_api(os.environ.get("TUSHARE_TOKEN", ""))


def _log(sync_id: str, mode: str, start: str, end: str, pulled: int, saved: int,
         duration_ms: int, status: str, error: str = "",
         failed_dates: list[str] | None = None, expected_days: int | None = None,
         actual_days: int | None = None):
    with get_conn() as conn:
        # 校验 sync_log 表存在
        global _sync_log_table_created
        if not _sync_log_table_created:
            conn.execute("SELECT 1 FROM sync_log LIMIT 1")
            _sync_log_table_created = True
        conn.execute(
            "INSERT INTO sync_log (sync_id, mode, start_date, end_date, rows_pulled, rows_saved, "
            "duration_ms, status, error, failed_dates, expected_days, actual_days) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (sync_id, mode, start, end, pulled, saved, duration_ms, status, error,
             ",".join(failed_dates) if failed_dates else "", expected_days, actual_days))
        conn.commit()


def _mark_running(sync_id: str, running: bool):
    status = "running" if running else "idle"
    with get_conn() as conn:
        conn.execute("UPDATE sync_config SET last_status=%s WHERE id=%s", (status, sync_id))
        conn.commit()


def _get_config(sync_id: str) -> dict:
    with get_conn() as conn:
        cur = conn.execute("SELECT id, name, tushare_api, pg_table, data_type, sync_mode, schedule, enabled, last_sync_date, last_sync_ts, last_status FROM sync_config WHERE id=%s", (sync_id,))
        row = cur.fetchone()
        if not row:
            return {}
        return {"id": row[0], "name": row[1], "api": row[2], "pg_table": row[3],
                "data_type": row[4], "mode": row[5], "schedule": row[6],
                "enabled": row[7], "last_sync_date": row[8],
                "last_sync_ts": row[9], "last_status": row[10]}


def _update_sync_state(sync_id: str, last_date: str, count: int, status: str = "idle"):
    """写终态（G-审 2026-08-18：status 支持 idle/partial/failed；调用方必须在本函数之后
    不再调 _mark_running(False)——后者会无条件覆盖回 'idle'）。"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE sync_config SET last_sync_date=%s, last_sync_ts=now(), last_sync_count=%s, last_status=%s WHERE id=%s",
            (last_date, count, status, sync_id))
        conn.commit()


def _alert_sync_failure(sync_id: str, status: str, failed_dates: list) -> None:
    """同步失败主动告警（G 建议 2026-08-18：F2 断 11 天才被发现的真因是无人盯 sync_log）。
    warn 级站内铃铛（notify 同标题 1min 去重），失败持续多轮靠 last_status=partial/failed 可见。"""
    try:
        from src.alert_notify.notify import notify
        notify("warn", "data", f"数据同步 {status}: {sync_id}",
               f"失败 {len(failed_dates)} 项：{'; '.join(failed_dates[:5])}"
               f"{'...' if len(failed_dates) > 5 else ''}。游标已按语义处理，缺口将自动重试；"
               f"持续失败请查 sync_log 详情。")
    except Exception as e:
        logger.warning("同步失败告警发送失败（不阻塞同步流程）: %s", e)


def _expected_trading_days(start: str, end: str) -> int:
    """从 trade_cal 算区间内 SSE 交易日数。查不到回退工作日数。"""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT count(*) FROM trade_cal WHERE exchange='SSE' AND is_open=1 "
                "AND cal_date >= %s AND cal_date <= %s", (start, end))
            cnt = cur.fetchone()[0] or 0
            if cnt > 0:
                return cnt
    except Exception:
        pass
    return len(pd.date_range(start=start, end=end, freq="B"))


# --- 同步调度入口 ---

def sync(sync_id: str, backfill_from: str | None = None,
         progress_cb: Callable | None = None) -> dict:
    """执行同步任务。

    Args:
        sync_id: 同步配置 ID
        backfill_from: 回补起始日期 YYYYMMDD。有值时回补历史，不推进 last_sync_date 游标。

    Returns: {status, rows_pulled, rows_saved, duration_ms, failed_dates, expected_days, actual_days}
    """
    cfg = _get_config(sync_id)
    if not cfg:
        return {"status": "error", "error": f"未知同步配置: {sync_id}"}
    if not cfg["enabled"]:
        return {"status": "skipped", "reason": "同步已禁用"}

    # 防重用心跳锁（进程被杀后 TTL 自然过期，不再卡死；last_status 只作展示，不作防重依据）
    from .sync_lock import SyncLock
    lock = SyncLock(sync_id)
    with lock:
        if not lock.acquired:
            return {"status": "skipped", "reason": "上次同步仍在运行"}

        _mark_running(sync_id, True)
        t0 = time.time()
        end_date = date.today().strftime("%Y%m%d")

        try:
            handler = _HANDLERS.get(sync_id)
            if not handler:
                # H-S1：路由表外 id（DB 行不受代码控制，真实可达）——原代码会以 0/0 假 success
                # 推进游标且触发 r 未定义 NameError（双记日志）。显式 error 返回，不动游标。
                duration_ms = int((time.time() - t0) * 1000)
                _log(sync_id, cfg["mode"], "", end_date, 0, 0, duration_ms,
                     "error", f"无 handler 路由: {sync_id}")
                _mark_running(sync_id, False)
                return {"status": "error", "error": f"无 handler 路由: {sync_id}",
                        "duration_ms": duration_ms}
            r = handler(cfg, end_date, backfill_from, progress_cb=progress_cb)
            pulled = r.get("pulled", 0)
            saved = r.get("saved", 0)
            start_date = r.get("start", end_date)
            failed_dates = r.get("failed_dates", [])
            expected_days = r.get("expected_days")
            actual_days = r.get("actual_days")
            status = "partial" if failed_dates else "success"
            duration_ms = int((time.time() - t0) * 1000)
            _log(sync_id, cfg["mode"], start_date, end_date, pulled, saved, duration_ms,
                 status, "", failed_dates, expected_days, actual_days)
            # ——— 游标推进（F2 根因收尾 2026-08-18，G 审修订 + H 修）———
            # 三态只作用于**返回 last_success_date 键**的 handler（_sync_by_trade_date 系：
            # astock_daily/etf_daily/astock_basic）。其余（分钟线=per-symbol 失败粒度、cb_daily、
            # list/trade_cal）维持无条件推进——分钟线若被卷入三态，200 积分下全市场失败会
            # 冻游标 → beat 每 30min 重试风暴。
            # 全失败仍刷 last_sync_ts（G-S3：调度器以旧 ts 算 next_run 会连发重试）。
            if not backfill_from:
                if "last_success_date" in r:
                    if failed_dates and r["last_success_date"] is None:
                        # 全失败：游标不动（旧值），标 failed——下轮增量自动重试整个窗口。
                        # H-S2：last_sync_date 为 NULL（新配置首同步即全失败）时 fallback 用
                        # 窗口起点**前一日**——写起点本身会让下轮 start=起点+1 永久跳过起点日
                        _fallback = cfg["last_sync_date"] or (
                            date.fromisoformat(
                                f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
                            ) - timedelta(days=1)).strftime("%Y%m%d")
                        _advance = (_fallback, 0, "failed")
                    elif failed_dates:
                        # 部分失败：推到**连续成功末日**（G-Q1a：最大成功日会永久跳过中间失败日；
                        # 后段已入库日下轮重拉由 upsert 幂等兜底）
                        _advance = (r["last_success_date"], saved, "partial")
                    else:
                        _advance = (end_date, saved, "idle")
                else:
                    _advance = (end_date, saved, "idle")
            else:
                _advance = None   # 回补不推进游标（现状）
            _mark_running(sync_id, False)   # G-S2：先清 running（置 idle），终态随后覆盖——
            #   若顺序颠倒，partial/failed 会被本行无条件覆盖回 idle（测试锁死此顺序）
            if _advance is not None:
                _update_sync_state(sync_id, *_advance)
            if failed_dates:
                # H 口径统一：告警用终态（failed/partial，与 sync_config.last_status 一致）
                _alert_sync_failure(sync_id, _advance[2] if _advance else status, failed_dates)
            return {"status": status, "rows_pulled": pulled, "rows_saved": saved,
                    "duration_ms": duration_ms, "failed_dates": failed_dates,
                    "expected_days": expected_days, "actual_days": actual_days,
                    "backfill": bool(backfill_from)}

        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            _log(sync_id, cfg["mode"], "", end_date, 0, 0, duration_ms, "error", str(e)[:200])
            _mark_running(sync_id, False)
            return {"status": "error", "error": str(e)[:200], "duration_ms": duration_ms}


# --- 通用按日批量拉取（去静默吞异常 + 完整性校验） ---

def _sync_by_trade_date(pro_api_fn: Callable, save_fn: Callable,
                        start: str, end: str, sleep_s: float = 0.5,
                        progress_cb: Callable | None = None) -> dict:
    """按交易日逐日批量拉取 + 写入。

    单日失败不中断整体，记入 failed_dates（含失败原因），不再静默 continue。
    用 trade_cal 校验预期交易日 vs 实际成功日。

    Returns: {pulled, saved, failed_dates, expected_days, actual_days}
    """
    date_range = pd.date_range(start=start, end=end, freq="B")
    total = len(date_range)
    total_pulled = 0
    total_saved = 0
    failed_dates: list[str] = []
    # F2 根因（G 审）：连续成功末日——第一个失败日之前的最后成功日；空 df 记成功（G-S4：
    # 节假日 freq="B" 会拉到空数据，若记失败游标永久卡死在节前）
    last_success_date: str | None = None
    broken = False

    for i, d in enumerate(date_range, 1):
        trade_date = d.strftime("%Y%m%d")
        try:
            df = pro_api_fn(trade_date=trade_date)
            if df is not None and not df.empty:
                if "trade_date" not in df.columns:
                    # 防御：异常响应缺关键列，给明确报错（避免下游 KeyError 隐晦）
                    failed_dates.append(f"{trade_date}:响应缺trade_date列,cols={list(df.columns)[:4]}")
                    broken = True
                    if progress_cb:
                        progress_cb(i, total, trade_date)
                    continue
                saved = save_fn(df)
                total_pulled += len(df)
                total_saved += saved
        except Exception as e:
            # 不再静默 continue：记失败日期 + 类型 + 原因，整体继续
            failed_dates.append(f"{trade_date}:{type(e).__name__}:{str(e)[:40]}")
            broken = True
        else:
            # 成功（含空 df）：仅在尚未出现失败时推进连续末日（broken 后不再追——重拉由幂等兜底）
            if not broken:
                last_success_date = trade_date
        if progress_cb:
            progress_cb(i, total, trade_date)
        if sleep_s:
            time.sleep(sleep_s)

    expected_days = _expected_trading_days(start, end)
    return {
        "pulled": total_pulled,
        "saved": total_saved,
        "failed_dates": failed_dates,
        "expected_days": expected_days,
        "actual_days": len(date_range) - len(failed_dates),
        "last_success_date": last_success_date,   # None=全失败；三态仅 sync() 对含此键者生效
    }


# --- 具体同步逻辑 ---

def _sync_astock_daily(cfg: dict, end_date: str, backfill_from: str | None = None,
                      progress_cb: Callable | None = None) -> dict:
    """A股日线同步（按日期批量拉取，一次全市场）。"""
    pro = _get_pro()
    if backfill_from:
        start = backfill_from
    else:
        last = cfg.get("last_sync_date") or (date.today() - timedelta(days=30)).strftime("%Y%m%d")
        start = (pd.Timestamp(last) + timedelta(days=1)).strftime("%Y%m%d")
        if start > end_date:
            return {"pulled": 0, "saved": 0, "start": last, "failed_dates": [], "expected_days": 0, "actual_days": 0}

    r = _sync_by_trade_date(pro.daily, _daily_to_save_fn, start, end_date,
                            progress_cb=progress_cb)
    r["start"] = start
    return r


def _sync_astock_basic(cfg: dict, end_date: str, backfill_from: str | None = None,
                       progress_cb: Callable | None = None) -> dict:
    """A股基本面指标同步（按日期批量拉取，一次全市场）。"""
    from src.data_platform.adapters.tushare_adapter import save_daily_basic
    pro = _get_pro()
    if backfill_from:
        start = backfill_from
    else:
        last = cfg.get("last_sync_date") or (date.today() - timedelta(days=7)).strftime("%Y%m%d")
        start = (pd.Timestamp(last) + timedelta(days=1)).strftime("%Y%m%d")
        if start > end_date:
            return {"pulled": 0, "saved": 0, "start": last, "failed_dates": [], "expected_days": 0, "actual_days": 0}

    r = _sync_by_trade_date(pro.daily_basic, lambda df: save_daily_basic(df), start, end_date,
                            progress_cb=progress_cb)
    r["start"] = start
    return r


def _sync_astock_list(cfg: dict, end_date: str, backfill_from: str | None = None,
                      progress_cb: Callable | None = None) -> dict:
    """A股股票列表全量同步。"""
    pro = _get_pro()
    df = pro.stock_basic(list_status="L")
    with get_conn() as conn:
        conn.execute("SELECT 1 FROM asset_static_info LIMIT 1")
        for _, r in df.iterrows():
            conn.execute("""
                INSERT INTO asset_static_info (ts_code, name, industry, market, list_status, list_date, delist_date)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ts_code) DO UPDATE SET name=EXCLUDED.name, industry=EXCLUDED.industry,
                    list_status=EXCLUDED.list_status
            """, (r.get("ts_code"), r.get("name"), r.get("industry"), r.get("market"),
                  r.get("list_status"), str(r.get("list_date","")), str(r.get("delist_date",""))))
        conn.commit()
    return {"pulled": len(df), "saved": len(df), "start": end_date,
            "failed_dates": [], "expected_days": None, "actual_days": None}


def _sync_cb_daily(cfg: dict, end_date: str, backfill_from: str | None = None,
                   progress_cb: Callable | None = None) -> dict:
    """可转债日线同步（全量拉取，cb_daily 不支持单标的）。"""
    from src.data_platform.adapters.tushare_adapter import pull_cb_daily, to_save_rows
    from src.data_platform.db import save_bars
    if backfill_from:
        start = backfill_from
    else:
        last = cfg.get("last_sync_date") or (date.today() - timedelta(days=30)).strftime("%Y%m%d")
        start = (pd.Timestamp(last) + timedelta(days=1)).strftime("%Y%m%d")
        if start > end_date:
            return {"pulled": 0, "saved": 0, "start": last, "failed_dates": [], "expected_days": 0, "actual_days": 0}

    df = pull_cb_daily(start, end_date)
    if df.empty:
        return {"pulled": 0, "saved": 0, "start": start, "failed_dates": [], "expected_days": 0, "actual_days": 0}
    rows = to_save_rows(df)
    saved = _save_bars(rows)
    return {"pulled": len(df), "saved": saved, "start": start,
            "failed_dates": [], "expected_days": None, "actual_days": None}


def _sync_cb_basic(cfg: dict, end_date: str, backfill_from: str | None = None,
                   progress_cb: Callable | None = None) -> dict:
    """可转债基本信息全量同步。"""
    pro = _get_pro()
    df = pro.cb_basic()
    with get_conn() as conn:
        conn.execute("SELECT 1 FROM cb_basic_info LIMIT 1")
        for _, r in df.iterrows():
            conn.execute("""
                INSERT INTO cb_basic_info (ts_code, bond_short_name, stk_code, stk_short_name,
                    maturity, par, issue_price, conv_price, conv_start_date, conv_end_date,
                    maturity_date, coupon_rate, rate_clause, list_date, delist_date)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ts_code) DO UPDATE SET bond_short_name=EXCLUDED.bond_short_name,
                    conv_price=EXCLUDED.conv_price, maturity_date=EXCLUDED.maturity_date,
                    rate_clause=EXCLUDED.rate_clause
            """, (r.get("ts_code"), r.get("bond_short_name"), r.get("stk_code"), r.get("stk_short_name"),
                  str(r.get("maturity","")), r.get("par"), r.get("issue_price"), r.get("conv_price"),
                  str(r.get("conv_start_date","")), str(r.get("conv_end_date","")),
                  str(r.get("maturity_date","")), r.get("coupon_rate"), r.get("rate_clause"),
                  str(r.get("list_date","")), str(r.get("delist_date",""))))
        conn.commit()
    return {"pulled": len(df), "saved": len(df), "start": end_date,
            "failed_dates": [], "expected_days": None, "actual_days": None}


def _sync_etf_daily(cfg: dict, end_date: str, backfill_from: str | None = None,
                   progress_cb: Callable | None = None) -> dict:
    """ETF日线同步（按日期批量拉取）。"""
    pro = _get_pro()
    if backfill_from:
        start = backfill_from
    else:
        last = cfg.get("last_sync_date") or (date.today() - timedelta(days=30)).strftime("%Y%m%d")
        start = (pd.Timestamp(last) + timedelta(days=1)).strftime("%Y%m%d")
        if start > end_date:
            return {"pulled": 0, "saved": 0, "start": last, "failed_dates": [], "expected_days": 0, "actual_days": 0}

    r = _sync_by_trade_date(pro.fund_daily, _daily_to_save_fn, start, end_date,
                            progress_cb=progress_cb)
    r["start"] = start
    return r


def _sync_etf_list(cfg: dict, end_date: str, backfill_from: str | None = None,
                   progress_cb: Callable | None = None) -> dict:
    """ETF基金列表全量同步。"""
    pro = _get_pro()
    df = pro.fund_basic(market="E")
    with get_conn() as conn:
        conn.execute("SELECT 1 FROM etf_basic_info LIMIT 1")
        for _, r in df.iterrows():
            conn.execute("""
                INSERT INTO etf_basic_info (ts_code, name, management, fund_type, invest_type, list_date)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ts_code) DO UPDATE SET name=EXCLUDED.name, management=EXCLUDED.management
            """, (r.get("ts_code"), r.get("name"), r.get("management"),
                  r.get("fund_type"), r.get("invest_type"), str(r.get("list_date",""))))
        conn.commit()
    return {"pulled": len(df), "saved": len(df), "start": end_date,
            "failed_dates": [], "expected_days": None, "actual_days": None}


def _sync_trade_cal(cfg: dict, end_date: str, backfill_from: str | None = None,
                    progress_cb: Callable | None = None) -> dict:
    """交易日历全量同步。"""
    from src.data_platform.adapters.tushare_adapter import pull_trade_cal
    year = date.today().year
    pull_trade_cal(year)
    return {"pulled": 365, "saved": 365, "start": end_date,
            "failed_dates": [], "expected_days": None, "actual_days": None}


_MINUTE_FREQ = {"astock_minute": "1min", "astock_minute_5min": "5min"}


def _sync_astock_minute(cfg: dict, end_date: str, backfill_from: str | None = None,
                        progress_cb: Callable | None = None) -> dict:
    """A股分钟线同步（per-symbol 循环 + stk_mins 分段，stk_mins 不支持按日全市场拉）。

    freq 由 sync_id 决定（astock_minute=1min / astock_minute_5min=5min）。
    增量：start = last_sync_date+1 ~ today；回补：start = backfill_from ~ today。
    逐只 _fetch_minute_and_save（内部分段，处理 stk_mins 8000 条限制）。
    """
    sync_id = cfg["id"]
    freq = _MINUTE_FREQ.get(sync_id)
    if freq is None:
        return {"pulled": 0, "saved": 0, "start": end_date, "failed_dates": [],
                "expected_days": None, "actual_days": None}

    if backfill_from:
        start = backfill_from
    else:
        last = cfg.get("last_sync_date") or (date.today() - timedelta(days=7)).strftime("%Y%m%d")
        start = (pd.Timestamp(last) + timedelta(days=1)).strftime("%Y%m%d")
        if start > end_date:
            return {"pulled": 0, "saved": 0, "start": last, "failed_dates": [],
                    "expected_days": 0, "actual_days": 0}

    ts_codes = _list_static_ts_codes("astock")
    total = len(ts_codes)
    total_pulled = 0
    total_saved = 0
    failed: list[str] = []
    for i, tc in enumerate(ts_codes, 1):
        try:
            df, saved = _fetch_minute_and_save(tc, freq, start, end_date)
            if df is not None and not df.empty:
                total_pulled += len(df)
                total_saved += saved
        except Exception as ex:
            failed.append(f"{tc}:{type(ex).__name__}:{str(ex)[:40]}")
        if progress_cb:
            progress_cb(i, total, tc)
        time.sleep(0.15)

    return {"pulled": total_pulled, "saved": total_saved, "start": start,
            "failed_dates": failed, "expected_days": None,
            "actual_days": total - len(failed)}


# --- 工具函数 ---

def _daily_to_rows(df: pd.DataFrame, adj_map: dict | None = None) -> list[tuple]:
    """通用 daily DataFrame -> bar_1D 行列表。adj_map={ts_code: 复权因子}（降级时 None→NULL）。"""
    from src.data_platform.schema import to_vt_symbol
    from src.data_platform.adapters.tushare_adapter import _safe_float
    adj_map = adj_map or {}
    rows = []
    for _, row in df.iterrows():
        ts_code = row.get("ts_code", "")
        vt_sym = to_vt_symbol(ts_code)
        trade_date = pd.Timestamp(row["trade_date"]).to_pydatetime()
        adj_raw = adj_map.get(ts_code)
        # 保 None（不套 _safe_float——其缺省 0.0，0 是合法因子值会毒化复权计算）
        adj_val = float(adj_raw) if adj_raw is not None and pd.notna(adj_raw) else None
        rows.append((
            vt_sym, "1D", trade_date,
            _safe_float(row["open"]), _safe_float(row["high"]), _safe_float(row["low"]),
            _safe_float(row["close"]),
            _safe_float(row.get("vol", 0)), _safe_float(row.get("amount", 0)),
            adj_val, "tushare",
        ))
    return rows


def _adj_map_for_df(df: pd.DataFrame) -> dict:
    """当日全市场复权因子 {ts_code: factor}。**降级返回 {}——同步继续，因子 NULL**
    （A/B-F1 契约：积分未到账不阻塞日线同步；到账后回补 adj_factor 即恢复）。"""
    try:
        from src.data_platform.adapters.tushare_adapter import pull_adj_factor_by_date
        td = str(df["trade_date"].iloc[0])
        fdf = pull_adj_factor_by_date(td)
        if fdf is None or fdf.empty:
            return {}
        return dict(zip(fdf["ts_code"], fdf["adj_factor"]))
    except Exception:
        return {}


def _save_bars(rows: list[tuple]) -> int:
    """写入 bar_1D 表。P3-15：save_bars 内部已有 validate_bars，这里补充 validate_bar_quality 调用。"""
    if not rows:
        return 0
    # P3-15: 调用 validate_bar_quality（去重/异常gap/vol=0）
    try:
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        quality = validate_bar_quality(rows)
        if quality.get("issues"):
            logger.warning(f"数据质量校验: {quality['issues']}")
    except Exception as e:
        logger.warning("validate_bar_quality 异常: %s", e)
    from src.data_platform.db import save_bars
    return save_bars("1D", rows)


def _daily_to_save_fn(df: pd.DataFrame) -> int:
    """daily DataFrame -> 行 -> 入库（_sync_by_trade_date 的 save_fn 适配）。"""
    return _save_bars(_daily_to_rows(df, _adj_map_for_df(df)))


def backfill_adj_factor(start_date: str | None = None, end_date: str | None = None,
                        progress_cb: Callable | None = None) -> dict:
    """回填 bar_1D 的 adj_factor（A/B-F1：历史全 NULL）。

    按交易日拉全市场因子 → 批量 UPDATE（只填 NULL 行，不覆盖非空）。
    E/F 盲审修订（2026-08-18）：
    - **只扫股票行**（F-S1：adj_factor 接口实测只含沪深京股票，ETF 因子在 fund_adj、转债无——
      不限定则回填永不收敛且每次全量空转 ~4h）
    - **范围谓词**（F-F1：`ts::date=%s` 用不上索引实测 3.68s/日，`ts>=d AND ts<d+1` 0.20s/日，18x）
    - updated 用 cursor.rowcount 真实受影响行数（F-S3：提交行数虚报含 0 命中）
    - **降级容错**（积分未到账）：首个交易日因子接口返回 None 即返回 degraded 状态，
      不抛异常——积分到账后重新触发即可。
    """
    from datetime import date as _date, timedelta as _td
    from src.data_platform.adapters.tushare_adapter import pull_adj_factor_by_date
    from src.data_platform.schema import to_vt_symbol

    with get_conn() as conn:
        sql = ("SELECT DISTINCT ts::date FROM bar_1d WHERE adj_factor IS NULL "
               # 只扫股票行：asset_static_info 是 A 股静态表（ETF/转债不在其中）
               "AND symbol IN (SELECT DISTINCT REPLACE(REPLACE(REPLACE(ts_code,'.SH','.SHSE'),"
               "'.SZ','.SZSE'),'.BJ','.BSE') FROM asset_static_info)")
        params: list = []
        if start_date:
            sql += " AND ts::date >= %s"
            params.append(start_date)
        if end_date:
            sql += " AND ts::date <= %s"
            params.append(end_date)
        cur = conn.execute(sql + " ORDER BY 1", params)
        dates = [r[0] for r in cur.fetchall()]
    if not dates:
        return {"status": "success", "days": 0, "updated": 0, "reason": "无 NULL 因子行（股票范围）"}

    updated = 0
    done = 0
    for d in dates:
        d = _date.fromisoformat(str(d)) if isinstance(d, str) else d
        next_d = d + _td(days=1)
        td = d.strftime("%Y%m%d")
        fdf = pull_adj_factor_by_date(td)
        if fdf is None:
            return {"status": "degraded", "days": len(dates), "processed": done, "updated": updated,
                    "reason": "复权因子接口不可用（积分未到账？）——已处理 %d/%d 日，到账后重新触发续填" % (done, len(dates))}
        if not fdf.empty:
            rows = [(float(f), to_vt_symbol(tc), d, next_d)
                    for tc, f in zip(fdf["ts_code"], fdf["adj_factor"]) if pd.notna(f)]
            if rows:
                with get_conn() as conn:
                    with conn.cursor() as cur:   # 池化连接无 executemany，走 cursor（同 db.py 模式）
                        cur.executemany(
                            "UPDATE bar_1d SET adj_factor=%s WHERE symbol=%s "
                            "AND ts >= %s AND ts < %s AND adj_factor IS NULL",
                            rows)
                    conn.commit()
                    rc = cur.rowcount
                    updated += rc if isinstance(rc, int) and rc > 0 else 0   # F-S3：真实受影响行数（驱动 -1/异常防御）
        done += 1
        if progress_cb:
            progress_cb(done, len(dates), td)
        time.sleep(0.3)   # 限速：adj_factor 按积分档限流，保守 200 次/分
    return {"status": "success", "days": len(dates), "processed": done, "updated": updated}


# --- 路由表 ---

_HANDLERS = {
    "astock_daily": _sync_astock_daily,
    "astock_basic": _sync_astock_basic,
    "astock_list": _sync_astock_list,
    "cb_daily": _sync_cb_daily,
    "cb_basic": _sync_cb_basic,
    "etf_daily": _sync_etf_daily,
    "etf_list": _sync_etf_list,
    "trade_cal": _sync_trade_cal,
    "astock_minute": _sync_astock_minute,
    "astock_minute_5min": _sync_astock_minute,
}


# ====================================================================
# per-symbol 同步 / 回补 / 删除（完整性驱动，非游标驱动）
# ====================================================================

# per-symbol 同步元数据：sync_id -> (freq, table, kind, bar_type)
#   freq:     K 线频率（bar 表 freq 列 + save_bars 表后缀）
#   table:    PG 表名（bar_1D / bar_1min / bar_5min）
#   kind:     标的来源（astock/etf/cb，对应静态信息表 + tushare api）
#   bar_type: daily（按交易日，pro.daily/fund_daily/cb_daily）/ minute（stk_mins per-symbol 拉取）
_PER_SYMBOL_META: dict[str, tuple[str, str, str, str]] = {
    "astock_daily":       ("1D",   "bar_1D",   "astock", "daily"),
    "etf_daily":          ("1D",   "bar_1D",   "etf",    "daily"),
    "cb_daily":           ("1D",   "bar_1D",   "cb",     "daily"),
    "astock_minute":      ("1min", "bar_1min", "astock", "minute"),
    "astock_minute_5min": ("5min", "bar_5min", "astock", "minute"),
}
_PER_SYMBOL_SYNC_IDS = set(_PER_SYMBOL_META)

# 分钟线 stk_mins 单次返回上限 8000 条，按频率算每段最大天数（1min 33 天 / 5min 166 天）
_BARS_PER_DAY = {"1min": 240, "5min": 48, "15min": 16, "30min": 8, "60min": 4}
_STK_MINS_MAX_BARS = 8000

# 各 sync_id 对应的：tushare 拉取 API / 静态信息表 / ts_code 来源
# astock_daily -> pro.daily(ts_code=) / asset_static_info
# etf_daily    -> pro.fund_daily(ts_code=) / etf_basic_info
# cb_daily     -> pro.cb_daily(ts_code=) / cb_basic_info（注：cb_daily 按日期拉全量更高效，per-symbol 仍支持）
# astock_minute/_5min -> pro.stk_mins(ts_code=,freq=) / asset_static_info（per-symbol only，不支持按日全市场）

_TUSHARE_MIN_DATE = os.environ.get("SYNC_START_DATE", "20100101")  # 全量起点，.env 可配（默认 2010）


def _split_minute_range(start: str, end: str, freq: str) -> list[tuple[str, str]]:
    """按 stk_mins 8000 条限制把日期区间分段（自然日粒度）。

    1min: 240 根/日 -> 33 天/段；5min: 48 根/日 -> 166 天/段。
    超过单段上限的区间拆成多段，每段单独调 stk_mins（避免单次返回被截断丢数据）。
    """
    bpd = _BARS_PER_DAY.get(freq, 240)
    max_days = max(1, _STK_MINS_MAX_BARS // bpd)
    days = pd.date_range(start=start, end=end, freq="D")
    if len(days) == 0:
        return []
    segs: list[tuple[str, str]] = []
    seg_start = start
    cnt = 0
    for d in days:
        cnt += 1
        if cnt >= max_days:
            segs.append((seg_start, d.strftime("%Y%m%d")))
            nxt = d + timedelta(days=1)
            seg_start = nxt.strftime("%Y%m%d")
            cnt = 0
    if seg_start <= end:
        segs.append((seg_start, end))
    return segs


def _get_pro_api(sync_id: str):
    """返回 (pro, api_fn, kind, freq, bar_type)。

    日线：api_fn = pro.daily/fund_daily/cb_daily（按 ts_code 拉取，返回 trade_date）。
    分钟线：api_fn = pro.stk_mins（per-symbol，返回 trade_time，需 freq + datetime 格式）。
    分钟线 per-symbol 拉取实际走 _fetch_minute_and_save（pull_minute），api_fn 仅作占位。
    不支持的 sync_id 返回 (pro, None, None, None, None)。
    """
    pro = _get_pro()
    meta = _PER_SYMBOL_META.get(sync_id)
    if meta is None:
        return pro, None, None, None, None
    freq, _table, kind, bar_type = meta
    if bar_type == "minute":
        return pro, pro.stk_mins, kind, freq, bar_type
    api_map = {"astock": pro.daily, "etf": pro.fund_daily, "cb": pro.cb_daily}
    return pro, api_map[kind], kind, freq, bar_type


def _list_static_ts_codes(kind: str) -> list[str]:
    """从静态信息表取全部 ts_code（Tushare 格式）。

    P3-14：优先读 static_symbols（P1-6 static_list_sync 写的表，含退市标记），
    fallback 到旧 asset_static_info / etf_basic_info / cb_basic_info。
    """
    # P3-14: 优先 static_symbols
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT ts_code FROM static_symbols WHERE coalesce(delisted, false) = false ORDER BY ts_code")
            rows = cur.fetchall()
        if rows:
            return [r[0] for r in rows]
    except (psycopg.errors.UndefinedTable, Exception) as e:
        logger.warning("查询 static_symbols 失败: %s", e)
    # fallback 旧表
    table = {"astock": "asset_static_info", "etf": "etf_basic_info", "cb": "cb_basic_info"}[kind]
    with get_conn() as conn:
        cur = conn.execute(f"SELECT ts_code FROM {table} ORDER BY ts_code")
        return [r[0] for r in cur.fetchall()]


def _get_list_date(kind: str, ts_code: str) -> str:
    """查某标的上市日（YYYYMMDD）。查不到回退 Tushare 最早 2010。"""
    table = {"astock": "asset_static_info", "etf": "etf_basic_info", "cb": "cb_basic_info"}[kind]
    with get_conn() as conn:
        cur = conn.execute(f"SELECT list_date FROM {table} WHERE ts_code=%s", (ts_code,))
        row = cur.fetchone()
    ld = row[0] if row else None
    if not ld or str(ld).strip() in ("", "None", "nan"):
        return _TUSHARE_MIN_DATE
    s = str(ld).strip()
    # 形如 19901219 / 1990-12-19
    s = s.replace("-", "").replace("/", "")
    if len(s) != 8 or not s.isdigit():
        return _TUSHARE_MIN_DATE
    # 早于 2010 的从 2010 起（Tushare daily 最早）
    return s if s >= _TUSHARE_MIN_DATE else _TUSHARE_MIN_DATE


def _local_bar_range(vt_symbol: str, table: str = "bar_1D") -> tuple[str | None, str | None, int]:
    """查指定 bar 表该标的本地首末日 + 条数。表不存在返回空（新库容错）。"""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                f"SELECT min(ts)::date, max(ts)::date, count(*) FROM {table} WHERE symbol=%s",
                (vt_symbol,))
            row = cur.fetchone()
    except psycopg.errors.UndefinedTable:
        return None, None, 0
    if not row or not row[0]:
        return None, None, 0
    return str(row[0]).replace("-", ""), str(row[1]).replace("-", ""), int(row[2])


def _local_trade_dates(vt_symbol: str, table: str = "bar_1D") -> list[str]:
    """查指定 bar 表该标的本地已有交易日列表（YYYYMMDD 升序，去重）。

    日线表每日一行；分钟线表每日多行，distinct date(ts) 去重。表不存在返回空。
    """
    try:
        with get_conn() as conn:
            cur = conn.execute(
                f"SELECT DISTINCT to_char(ts, 'YYYYMMDD') FROM {table} "
                f"WHERE symbol=%s ORDER BY 1",
                (vt_symbol,))
            return [r[0] for r in cur.fetchall()]
    except psycopg.errors.UndefinedTable:
        return []


def _expected_trade_dates(api_fn, start: str, end: str) -> list[str]:
    """算区间内预期 A 股交易日（YYYYMMDD 升序）。

    优先 trade_cal（DB 多年）-> pro.trade_cal 按年拉补 -> 回退工作日。
    """
    # 1. trade_cal DB（逐年，可能不全）
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT to_char(cal_date, 'YYYYMMDD') FROM trade_cal "
            "WHERE exchange='SSE' AND is_open=1 AND cal_date >= %s AND cal_date <= %s ORDER BY cal_date",
            (start, end))
        dates = [r[0] for r in cur.fetchall()]
    if dates:
        return dates
    # 2. trade_cal DB 没覆盖该区间 -> pro.trade_cal 按年拉
    try:
        pro = _get_pro()
        start_y = int(start[:4]); end_y = int(end[:4])
        all_d = []
        for y in range(start_y, end_y + 1):
            df = pro.trade_cal(exchange="SSE", start_date=f"{y}0101", end_date=f"{y}1231")
            if df is not None and not df.empty:
                all_d.extend([str(d) for d in df[df["is_open"] == 1]["cal_date"].tolist()])
        if all_d:
            all_d.sort()
            return all_d
    except Exception:
        pass
    # 3. 回退：工作日（pandas freq=B）
    return [d.strftime("%Y%m%d") for d in pd.date_range(start=start, end=end, freq="B")]


def _find_gaps(api_fn, kind: str, ts_code: str, first: str, last: str,
               table: str = "bar_1D") -> list[tuple[str, str]]:
    """找该标的的缺口段（按交易日粒度，日线/分钟线通用）。

    扫描范围只限本地数据区间（first~last）+ 尾部（last~今天），不扫上市日到今天全程
    （老股全程要调几十次 trade_cal，且本地已有数据说明那区间基本完整）。
    若要补上市日到 first 之间的早期缺口，用回补或全量重建。

    比对预期交易日（trade_cal）与本地已有交易日，返回连续缺口段 [(start,end), ...]。
    分钟线同样按交易日找缺口（一天多根视为一天有数据），缺口段再交给分钟线拉取按 stk_mins 限制分小段。
    """
    from src.data_platform.schema import to_vt_symbol
    vt = to_vt_symbol(ts_code)
    today = date.today().strftime("%Y%m%d")

    # 扫描区间：本地首日 ~ 今天
    if first is None:
        return []  # 无本地数据，sync_symbol 的 cnt==0 分支已处理全量
    scan_start = first
    scan_end = today
    expected = _expected_trade_dates(api_fn, scan_start, scan_end)
    local_set = set(_local_trade_dates(vt, table))

    missing = [d for d in expected if d not in local_set]
    if not missing:
        return []

    # 连续交易日合并成段
    missing.sort()
    gaps = []
    seg_start = missing[0]
    prev_idx = expected.index(missing[0])
    for d in missing[1:]:
        idx = expected.index(d)
        if idx == prev_idx + 1:
            prev_idx = idx
            continue
        gaps.append((seg_start, expected[prev_idx]))
        seg_start = d
        prev_idx = idx
    gaps.append((seg_start, expected[prev_idx]))
    return gaps


def _fetch_and_save(api_fn, ts_code: str, start: str, end: str, save_fn) -> "pd.DataFrame | None":
    """拉取一段日期数据并入库。返回 df（供调用方统计）。"""
    try:
        df = api_fn(ts_code=ts_code, start_date=start, end_date=end)
    except Exception:
        return None
    if df is None or df.empty:
        return df
    if "trade_date" not in df.columns:
        return None
    rows = _daily_to_rows(df)
    save_fn(rows)
    return df


def _wrap_result(df, used: str, cnt: int, start: str, end: str) -> dict:
    """封装 full/empty/error 结果。"""
    if df is None:
        return {"status": "empty", "pulled": 0, "saved": 0,
                "range": [start, end], "mode_used": used, "local_count_before": cnt}
    if df.empty:
        return {"status": "empty", "pulled": 0, "saved": 0,
                "range": [start, end], "mode_used": used, "local_count_before": cnt}
    actual_first = str(df["trade_date"].min())
    actual_last = str(df["trade_date"].max())
    return {"status": "success", "pulled": len(df), "saved": len(df),
            "range": [actual_first, actual_last], "mode_used": used,
            "local_count_before": cnt}


def _fetch_minute_and_save(ts_code: str, freq: str, start: str, end: str,
                           overwrite: bool = False) -> "tuple[pd.DataFrame | None, int]":
    """分钟线分段拉取入库（stk_mins per-symbol + 8000 条分段）。

    返回 (concat_df, saved)。overwrite=True 覆盖写（回补），False 冲突跳过（增量/全量）。
    每段单独调 pull_minute（按 09:00~15:00 交易时段），避免单次返回被截断丢数据。
    """
    from src.data_platform.adapters.tushare_adapter import pull_minute, to_save_rows_min
    from src.data_platform.db import save_bars, save_bars_overwrite
    save_fn = save_bars_overwrite if overwrite else save_bars
    total_df: list[pd.DataFrame] = []
    total_saved = 0
    for s, e in _split_minute_range(start, end, freq):
        df = pull_minute(ts_code, freq, f"{s} 09:00:00", f"{e} 15:00:00")
        if df is None or df.empty:
            continue
        rows = to_save_rows_min(df, freq)
        total_saved += save_fn(freq, rows)
        total_df.append(df)
    if not total_df:
        return None, 0
    return pd.concat(total_df, ignore_index=True), total_saved


def _wrap_minute_result(df, used: str, cnt: int, start: str, end: str) -> dict:
    """分钟线结果封装（pulled/saved 用 len(df)，与日线 _wrap_result 一致）。"""
    if df is None or df.empty:
        return {"status": "empty", "pulled": 0, "saved": 0,
                "range": [start, end], "mode_used": used, "local_count_before": cnt}
    actual_first = str(df["trade_time"].min())[:10].replace("-", "")
    actual_last = str(df["trade_time"].max())[:10].replace("-", "")
    return {"status": "success", "pulled": len(df), "saved": len(df),
            "range": [actual_first, actual_last], "mode_used": used,
            "local_count_before": cnt}


def sync_symbol(sync_id: str, ts_code: str, mode: str = "auto") -> dict:
    """单标的智能同步（完整性驱动）。

    mode='auto'：空 -> 从上市日起全量；有数据 -> 找缺口段逐段补。
    日线按 trade_cal 找缺失交易日；分钟线同样按交易日找缺口（再按 stk_mins 限制分小段拉）。
    返回 {status, pulled, saved, range:[首,末], mode_used}
    """
    from src.data_platform.schema import to_vt_symbol
    pro, api_fn, kind, freq, bar_type = _get_pro_api(sync_id)
    if kind is None:
        return {"status": "error", "error": f"不支持 per-symbol 同步: {sync_id}"}

    today = date.today().strftime("%Y%m%d")
    vt = to_vt_symbol(ts_code)
    table = _PER_SYMBOL_META[sync_id][1]
    first, last, cnt = _local_bar_range(vt, table)

    # 分钟线分支：stk_mins per-symbol + 8000 条分段
    if bar_type == "minute":
        if mode == "auto":
            if cnt == 0:
                start = _get_list_date(kind, ts_code)
                df, _saved = _fetch_minute_and_save(ts_code, freq, start, today)
                return _wrap_minute_result(df, "full", cnt, start, today)
            gaps = _find_gaps(api_fn, kind, ts_code, first, last, table=table)
            total_pulled = 0
            gap_ranges = []
            for g_start, g_end in gaps:
                df, _s = _fetch_minute_and_save(ts_code, freq, g_start, g_end)
                if df is not None and not df.empty:
                    total_pulled += len(df)
                    gap_ranges.append([g_start, g_end])
            return {"status": "uptodate" if not gap_ranges else "success",
                    "pulled": total_pulled, "saved": total_pulled,
                    "range": [first, last], "mode_used": "incremental",
                    "gaps_filled": gap_ranges, "local_count_before": cnt}
        elif mode == "full":
            start = _get_list_date(kind, ts_code)
            df, _saved = _fetch_minute_and_save(ts_code, freq, start, today)
            return _wrap_minute_result(df, "full", cnt, start, today)
        return {"status": "error", "error": f"未知 mode: {mode}"}

    # 日线分支（原逻辑）
    if mode == "auto":
        if cnt == 0:
            # 空 -> 从上市日起全量
            start = _get_list_date(kind, ts_code)
            used = "full"
            df = _fetch_and_save(api_fn, ts_code, start, today, _save_bars)
            return _wrap_result(df, used, cnt, start, today)
        else:
            # 有数据 -> 完整性扫描：找出上市日到今天所有缺口段，逐段补
            gaps = _find_gaps(api_fn, kind, ts_code, first, last)
            total_pulled = 0
            total_saved = 0
            gap_ranges = []
            for g_start, g_end in gaps:
                df = _fetch_and_save(api_fn, ts_code, g_start, g_end, _save_bars)
                if df is not None and not df.empty:
                    total_pulled += len(df)
                    total_saved += _save_bars(_daily_to_rows(df))
                    gap_ranges.append([g_start, g_end])
            used = "incremental"
            return {"status": "uptodate" if not gap_ranges else "success",
                    "pulled": total_pulled, "saved": total_saved,
                    "range": [first, last], "mode_used": used,
                    "gaps_filled": gap_ranges, "local_count_before": cnt}
    elif mode == "full":
        start = _get_list_date(kind, ts_code)
        used = "full"
        df = _fetch_and_save(api_fn, ts_code, start, today, _save_bars)
        return _wrap_result(df, used, cnt, start, today)
    else:
        return {"status": "error", "error": f"未知 mode: {mode}"}


def backfill_symbol(sync_id: str, ts_code: str, start: str, end: str) -> dict:
    """单标的回补：用户指定范围重新下载，覆盖本地已有（DO UPDATE）。

    体现手动回补优先级高于增量：本地已有数据也用拉取的新值覆盖。
    """
    from src.data_platform.schema import to_vt_symbol
    from src.data_platform.db import save_bars_overwrite
    pro, api_fn, kind, freq, bar_type = _get_pro_api(sync_id)
    if kind is None:
        return {"status": "error", "error": f"不支持 per-symbol 回补: {sync_id}"}

    # 分钟线分支：分段 stk_mins + 覆盖写
    if bar_type == "minute":
        df, _saved = _fetch_minute_and_save(ts_code, freq, start, end, overwrite=True)
        if df is None or df.empty:
            return {"status": "empty", "pulled": 0, "saved": 0, "range": [start, end]}
        actual_first = str(df["trade_time"].min())[:10].replace("-", "")
        actual_last = str(df["trade_time"].max())[:10].replace("-", "")
        return {"status": "success", "pulled": len(df), "saved": len(df),
                "range": [actual_first, actual_last], "overwritten": True}

    # 日线分支（原逻辑）
    try:
        df = api_fn(ts_code=ts_code, start_date=start, end_date=end)
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {str(e)[:120]}"}

    if df is None or df.empty:
        return {"status": "empty", "pulled": 0, "saved": 0, "range": [start, end]}

    if "trade_date" not in df.columns:
        return {"status": "error", "error": f"响应缺 trade_date 列: {list(df.columns)[:4]}"}

    # F-F2：单标的回补也带因子——_daily_to_rows 不传 adj_map 时全 NULL，叠加 overwrite 的
    # DO UPDATE 会把已回填的 adj_factor 清回 NULL（E/F 盲审实测的数据破坏路径）；
    # 拉不到因子（降级）时 COALESCE 兜底不清空（schema.py BAR_TABLE_INSERT_OVERWRITE）
    try:
        from src.data_platform.adapters.tushare_adapter import pull_adj_factor_by_code
        fdf = pull_adj_factor_by_code(ts_code, start, end)
        adj_map = (dict(zip(fdf["ts_code"], fdf["adj_factor"]))
                   if fdf is not None and not fdf.empty else {})
    except Exception:
        adj_map = {}
    rows = _daily_to_rows(df, adj_map)
    saved = save_bars_overwrite("1D", rows)  # 覆盖
    actual_first = str(df["trade_date"].min())
    actual_last = str(df["trade_date"].max())
    return {"status": "success", "pulled": len(df), "saved": saved,
            "range": [actual_first, actual_last], "overwritten": True}


def delete_symbol(sync_id: str, ts_code: str) -> dict:
    """删除单标的本地数据。再次同步即完整重建。日线删 bar_1D，分钟线删 bar_1min/5min。"""
    from src.data_platform.schema import to_vt_symbol
    meta = _PER_SYMBOL_META.get(sync_id)
    if meta is None:
        return {"status": "error", "error": f"不支持 per-symbol 删除: {sync_id}"}
    table = meta[1]
    vt = to_vt_symbol(ts_code)
    with get_conn() as conn:
        try:
            cur = conn.execute(f'DELETE FROM {table} WHERE symbol=%s', (vt,))
            deleted = cur.rowcount
            conn.commit()
        except psycopg.errors.UndefinedTable:
            deleted = 0
            conn.commit()
    return {"status": "success", "deleted": deleted, "symbol": vt}


def sync_all(sync_id: str, progress_cb: Callable | None = None) -> dict:
    """全市场全量同步（Celery 调用）。

    遍历该类型全部标的，逐只 sync_symbol(auto)。progress_cb(i, total, ts_code) 写进度。
    """
    pro, api_fn, kind, freq, bar_type = _get_pro_api(sync_id)
    if kind is None:
        return {"status": "error", "error": f"不支持全量同步: {sync_id}"}

    ts_codes = _list_static_ts_codes(kind)
    total = len(ts_codes)
    ok = 0
    failed: list[str] = []
    total_saved = 0

    for i, tc in enumerate(ts_codes, 1):
        try:
            # 全量重建：强制 full（从上市日起全历史），不走 auto 完整性扫描
            # （auto 只补 first~last 缺口，不补上市日到 first 的早期缺口）
            r = sync_symbol(sync_id, tc, mode="full")
            if r.get("status") in ("success", "uptodate", "empty"):
                ok += 1
                total_saved += r.get("saved", 0)
            else:
                failed.append(f"{tc}:{r.get('error','')[:40]}")
        except Exception as e:
            failed.append(f"{tc}:{type(e).__name__}:{str(e)[:40]}")
        if progress_cb:
            progress_cb(i, total, tc)
        # 限频友好：每只之间小睡
        time.sleep(0.15)

    return {"status": "partial" if failed else "success",
            "total": total, "ok": ok, "failed_count": len(failed),
            "saved": total_saved, "failed": failed[:20]}


def list_symbols(sync_id: str, q: str = "", page: int = 1, size: int = 9999) -> dict:
    """列出某类型全部标的 + 本地数据状态（批量聚合查 bar_1D，避免逐只查）。

    Returns: {items:[{ts_code,name,list_date,local_count,local_first,local_last}], total}
    """
    from src.data_platform.schema import to_vt_symbol
    _, _, kind, _freq, _bar_type = _get_pro_api(sync_id)
    if kind is None:
        return {"items": [], "total": 0}
    table = {"astock": "asset_static_info", "etf": "etf_basic_info", "cb": "cb_basic_info"}[kind]
    name_col = "bond_short_name" if kind == "cb" else "name"
    bar_table = _PER_SYMBOL_META[sync_id][1]

    q_escaped = q.replace('%', '\\%').replace('_', '\\_') if q else ""
    like = f"%{q_escaped}%" if q else "%"
    with get_conn() as conn:
        with conn.transaction():
            cur = conn.execute(
                f"SELECT ts_code, {name_col}, list_date FROM {table} "
                f"WHERE ts_code ILIKE %s OR {name_col} ILIKE %s "
                f"ORDER BY ts_code LIMIT %s OFFSET %s",
                (like, like, size, (page - 1) * size))
            rows = cur.fetchall()
            cur = conn.execute(
                f"SELECT count(*) FROM {table} WHERE ts_code ILIKE %s OR {name_col} ILIKE %s",
                (like, like))
            total = cur.fetchone()[0] or 0

    if not rows:
        return {"items": [], "total": total}

    # 批量聚合查 bar_1D 本地数据范围（一次 ANY 查询，非逐只）
    vts = {to_vt_symbol(r[0]): r[0] for r in rows}
    local: dict = {}
    try:
        with get_conn() as conn:
            cur = conn.execute(
                f"SELECT symbol, count(*), min(ts), max(ts) FROM {bar_table} "
                "WHERE symbol = ANY(%s) GROUP BY symbol",
                (list(vts.keys()),))
            for sym, cnt, mn, mx in cur.fetchall():
                local[sym] = (int(cnt),
                              str(mn).replace("-", "")[:8] if mn else None,
                              str(mx).replace("-", "")[:8] if mx else None)
    except psycopg.errors.UndefinedTable:
        pass

    items = []
    for ts_code, name, list_date in rows:
        vt = to_vt_symbol(ts_code)
        loc = local.get(vt)
        items.append({
            "ts_code": ts_code, "name": name,
            "list_date": str(list_date) if list_date else "",
            "local_count": loc[0] if loc else 0,
            "local_first": loc[1] if loc else None,
            "local_last": loc[2] if loc else None,
        })
    return {"items": items, "total": total}
