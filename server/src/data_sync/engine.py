"""数据同步引擎 -- 通用增量/全量同步，按 sync_config.id 调度。

支持8种数据类型：astock_daily / astock_basic / astock_list /
cb_daily / cb_basic / etf_daily / etf_list / trade_cal

回补：sync(sync_id, backfill_from=YYYYMMDD) 回补历史缺口，不推进 last_sync_date 游标。
完整性：按日拉取的同步用 trade_cal 校验预期交易日 vs 实际成功日，缺口暴露在 sync_log。
"""

from __future__ import annotations
from src.data_platform.db import get_conn
from src.data_platform.security_master import normalize_board
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
    """从 external_interface 数据域行读 Tushare（DB 优先，.env fallback）。静态/日历/三档专用。"""
    from src.data_platform.data_source import get_data_source
    ds = get_data_source("tushare")
    if ds:
        ds.record_usage(provider="tushare", api_name="get_pro")
        return ds.get_client()
    import tushare as ts
    return ts.pro_api(os.environ.get("TUSHARE_TOKEN", ""))


def _get_kline_adapter(cfg: dict):
    """K 线数据源 adapter（按 sync_config.provider 路由，24 号多数据源架构）。

    静态/日历/三档仍走 _get_pro（Tushare 专属）；K 线走 adapter（可插拔）。
    未知 provider 回退 tushare + 告警（盲审 A-P2/B-P2：配置错 provider 不应打断同步）。
    批 57 M2 试点（盲审 A/B 修后收窄）：system_config 键 routing_kline_pilot=on 时
    **仅日线数据面同步**（sync_id 以 _daily 结尾）走 resolve(supply) 选源；分钟/基准/复权
    因子路径保持原 cfg（kind 波及面+归一面混搭两因——A P1 b/c）。试点回退=键置 off；
    resolve 全程 try 包裹（表缺/DB 抖动回退 cfg provider——回退语义自洽）。
    已知占位：symbols=("600000.SHSE",) 单标的近似（exchanges 限定行不可判——正式化取真实标的集）。
    """
    from src.data_platform.adapters.base import get_adapter
    provider = cfg.get("provider") or "tushare"
    if _routing_pilot_on() and str(cfg.get("id", "")).endswith("_daily"):
        try:
            from src.quant_common.contract import DataRequest
            from src.data_platform import routing
            chain = routing.resolve(DataRequest(
                kind="bar_daily", symbols=("600000.SHSE",), temporality="historical",
                consumer_tag="sync", mode="supply"))
            for c in chain.candidates:             # supply 链无 local_pg（28 §7.1）——首选外部源
                if not c.is_local:
                    provider = c.adapter
                    break
            logger.info("M2 试点路由：%s supply 链首选 provider=%s（epoch=%s）",
                        cfg.get("id"), provider, chain.epoch)
        except Exception as e:
            logger.warning("M2 试点路由失败，回退 cfg provider=%s: %s", provider, e)
    try:
        return get_adapter(provider)
    except ValueError:
        logger.warning("未注册的数据源 provider=%s，回退 tushare", provider)
        return get_adapter("tushare")


def _routing_pilot_on() -> bool:
    """试点开关（system_config 键；读失败=off——回退现状路径）。"""
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT value FROM system_config WHERE key='routing_kline_pilot'")
            r = cur.fetchone()
            return bool(r) and str(r[0]).lower() in ("on", "1", "true")
    except Exception:
        return False


def _sync_kind_whitelist() -> set[str]:
    """批 58·M3 灰度白名单（system_config 键 sync_kind_routing，逗号分隔 sync_id）。

    空/缺省=off（全走 _HANDLERS）。读失败=空集（回退现状路径）。55a 式按 sync_id 逐项切。
    """
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT value FROM system_config WHERE key='sync_kind_routing'")
            r = cur.fetchone()
            if not r:
                return set()
            return {s.strip() for s in str(r[0]).split(",") if s.strip()}
    except Exception:
        return set()


def _preserve_normalization() -> bool:
    """批 58b：迁移期特征开关（29 §六）。system_config 键 preserve_current_normalization。

    True（默认，键不存在）=竞价条保留现行原样落库（58 收编期行为等价）；
    False（键=off/0/false）=启用归一义务①竞价条并入首根（58b 数据变更）。读失败=True。
    """
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT value FROM system_config WHERE key='preserve_current_normalization'")
            r = cur.fetchone()
            return not (bool(r) and str(r[0]).lower() in ("off", "0", "false"))
    except Exception:
        return True


def _get_rate_ds(provider: str):
    """按 provider 选限速/熔断 DataSource（fallback tushare）。

    盲审 A-P1-3：rate_limit_context 的 ds 原硬编码 tushare，切 provider 后限速/熔断串源——
    新源仍按 Tushare 间隔限速、熔断器 key=tushare。改为按 provider 选。
    """
    from src.data_platform.data_source import get_data_source, TushareDataSource
    return get_data_source(provider) or TushareDataSource()


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
        cur = conn.execute("SELECT id, name, tushare_api, pg_table, data_type, sync_mode, schedule, enabled, last_sync_date, last_sync_ts, last_status, provider FROM sync_config WHERE id=%s", (sync_id,))
        row = cur.fetchone()
        if not row:
            return {}
        return {"id": row[0], "name": row[1], "api": row[2], "pg_table": row[3],
                "data_type": row[4], "mode": row[5], "schedule": row[6],
                "enabled": row[7], "last_sync_date": row[8],
                "last_sync_ts": row[9], "last_status": row[10], "provider": row[11]}


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
               f"持续失败请查 sync_log 详情。", code="sync.status")
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
            handler = _sync_via_kind if sync_id in _sync_kind_whitelist() else _HANDLERS.get(sync_id)
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

def _api_name_of(cfg: dict) -> str:
    """cfg["api"]（如 'pro.daily'）→ 接口名（'daily'，与 rate_limits 键词汇表对齐）。"""
    return str(cfg.get("api", "")).rsplit(".", 1)[-1] or "daily"


def _sync_by_trade_date(pro_api_fn: Callable, save_fn: Callable,
                        start: str, end: str, sleep_s: float | None = None,
                        progress_cb: Callable | None = None,
                        api_name: str = "daily", provider: str = "tushare") -> dict:
    """按交易日逐日批量拉取 + 写入。

    单日失败不中断整体，记入 failed_dates（含失败原因），不再静默 continue。
    用 trade_cal 校验预期交易日 vs 实际成功日。

    限速（限流治理吸收 2026-08-27）：循环间 sleep 收编 rate_limit_context——间隔从
    DataSource 三级取；sleep_s 显式传入则覆盖间隔（兼容旧调用，测试传 0 关闭等待），
    None=走配置。api_name=接口名（_api_name_of）决定用哪档。

    Returns: {pulled, saved, failed_dates, expected_days, actual_days}
    """
    from src.data_platform.rate_limit import rate_limit_context
    ds = _get_rate_ds(provider)   # 按 provider 选限速/熔断 DataSource（24 号，不串源）
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
            with rate_limit_context(ds, api_name, min_interval=sleep_s):
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
    adapter = _get_kline_adapter(cfg)
    if backfill_from:
        start = backfill_from
    else:
        last = cfg.get("last_sync_date") or (date.today() - timedelta(days=30)).strftime("%Y%m%d")
        start = (pd.Timestamp(last) + timedelta(days=1)).strftime("%Y%m%d")
        if start > end_date:
            return {"pulled": 0, "saved": 0, "start": last, "failed_dates": [], "expected_days": 0, "actual_days": 0}

    r = _sync_by_trade_date(
        lambda trade_date: adapter.pull_daily_batch(trade_date, "astock"),
        lambda df: _daily_to_save_fn(df, adapter),
        start, end_date, api_name=_api_name_of(cfg), progress_cb=progress_cb,
        provider=adapter.provider)
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
                            api_name=_api_name_of(cfg), progress_cb=progress_cb)
    r["start"] = start
    return r


def _ts_to_vt_prefix(ts_code: str) -> str:
    """ts_code→vt_symbol 前缀+交易所（与 0092 迁移同映射——真源 schema.to_vt_symbol）。"""
    from src.data_platform.schema import to_vt_symbol
    try:
        return to_vt_symbol(ts_code)
    except Exception as e:
        logger.warning("to_vt_symbol 转换失败（回退原码——将落不匹配词表的脏 exchange）: %r %s", ts_code, e)
        return ts_code


def _sm_upsert(rows) -> None:
    """批 56a·M1 填充链：security_master upsert（fail-soft——SM 失败不打断数据同步）。

    rows 为惰性 iterable（生成器在函数体内才求值）——行构造的脏值异常
    （float/rsplit 等）同样落在 try 内，不会穿透打断主同步（盲审 A/B）。
    """
    try:
        rows = list(rows)
        if not rows:
            return
        from src.data_platform.security_master import SMClient
        SMClient().upsert_rows(rows)
    except Exception as e:
        logger.warning("security_master 填充失败（同步主流程不受影响）: %s", e, exc_info=True)


def _sm_upsert_state(rows) -> None:
    """批 56a·M1 填充链：security_state 时变行 upsert（fail-soft+惰性求值同 _sm_upsert）。"""
    try:
        rows = list(rows)
        if not rows:
            return
        from src.data_platform.security_master import SMClient
        SMClient().upsert_state(rows)
    except Exception as e:
        logger.warning("security_state 填充失败（同步主流程不受影响）: %s", e, exc_info=True)


def _sync_astock_list(cfg: dict, end_date: str, backfill_from: str | None = None,
                      progress_cb: Callable | None = None) -> dict:
    """A股股票列表全量同步。"""
    pro = _get_pro()
    df = pro.stock_basic(list_status="L")   # DB 优化：网络拉取在事务外（2026-08-21 盘点）
    rows = [(r.get("ts_code"), r.get("name"), r.get("industry"), r.get("market"),
             r.get("list_status") or "L", str(r.get("list_date", "")), str(r.get("delist_date", "")))
            for r in df.to_dict("records")]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO asset_static_info (ts_code, name, industry, market, list_status, list_date, delist_date)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ts_code) DO UPDATE SET name=EXCLUDED.name, industry=EXCLUDED.industry,
                    list_status=EXCLUDED.list_status
            """, rows)
        conn.commit()
    # 品类值随行携带（盲审 A：server_default 按品类错——转债应 T+0/乘数 10）；
    # (vt.rsplit+[""])[1]=无后缀安全提取（脏 ts_code 落 try 内 fail-soft）
    _sm_upsert(((vt := _ts_to_vt_prefix(r[0])), "astock",
                (vt.rsplit(".", 1) + [""])[1], "stock", normalize_board(r[3]), r[1], r[2],
                100, 0.01, "T+1", r[5], r[6])
               for r in rows if r[0])
    return {"pulled": len(df), "saved": len(df), "start": end_date,
            "failed_dates": [], "expected_days": None, "actual_days": None}


def _sync_cb_daily(cfg: dict, end_date: str, backfill_from: str | None = None,
                   progress_cb: Callable | None = None) -> dict:
    """可转债日线同步（全量拉取，cb_daily 不支持单标的）。"""
    from src.data_platform.adapters.tushare_adapter import pull_cb_daily
    adapter = _get_kline_adapter(cfg)
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
    rows = adapter.to_bar_rows(df, "1D")
    saved = _save_bars(rows)
    return {"pulled": len(df), "saved": saved, "start": start,
            "failed_dates": [], "expected_days": None, "actual_days": None}


def _sync_cb_basic(cfg: dict, end_date: str, backfill_from: str | None = None,
                   progress_cb: Callable | None = None) -> dict:
    """可转债基本信息全量同步。"""
    pro = _get_pro()
    from src.data_platform.rate_limit import rate_limit_context
    with rate_limit_context(_get_rate_ds("tushare"), "cb_basic"):   # 批 64b 裸调收编（档 0.3s）
        df = pro.cb_basic()   # DB 优化：拉取在事务外
    rows = [(r.get("ts_code"), r.get("bond_short_name"), r.get("stk_code"), r.get("stk_short_name"),
             str(r.get("maturity", "")), r.get("par"), r.get("issue_price"), r.get("conv_price"),
             str(r.get("conv_start_date", "")), str(r.get("conv_end_date", "")),
             str(r.get("maturity_date", "")), r.get("coupon_rate"), r.get("rate_clause"),
             str(r.get("list_date", "")), str(r.get("delist_date", "")))
            for r in df.to_dict("records")]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO cb_basic_info (ts_code, bond_short_name, stk_code, stk_short_name,
                    maturity, par, issue_price, conv_price, conv_start_date, conv_end_date,
                    maturity_date, coupon_rate, rate_clause, list_date, delist_date)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ts_code) DO UPDATE SET bond_short_name=EXCLUDED.bond_short_name,
                    conv_price=EXCLUDED.conv_price, maturity_date=EXCLUDED.maturity_date,
                    rate_clause=EXCLUDED.rate_clause
            """, rows)
        conn.commit()
    _sm_upsert(((vt := _ts_to_vt_prefix(r[0])), "astock",
                (vt.rsplit(".", 1) + [""])[1], "convertible", None, r[1], None,
                10, 0.001, "T+0", r[12], r[13])
               for r in rows if r[0])
    import json as _json
    def _norm_date(s, fallback="") -> str:
        """YYYYMMDD→YYYY-MM-DD；脏值（空串/'None'/NaN）回落 fallback（发行日 list_date）再兜 2010-01-01。"""
        def _ok(v):
            return v if isinstance(v, str) and v.isdigit() and len(v) == 8 else None
        s, fb = _ok((s or "").strip() if isinstance(s, str) else ""), _ok(
            (fallback or "").strip() if isinstance(fallback, str) else "")
        v = s or fb
        return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if v else "2010-01-01"
    # NaN 过滤（盲审 B：NaN 进 json.dumps 产非法 JSON 毒化整批）
    _sm_upsert_state(((vt := _ts_to_vt_prefix(r[0])), _norm_date(r[8], r[12]),
                       "conv_price", _json.dumps({"conv_price": float(r[7])}))
                      for r in rows if r[0] and r[7] is not None and not pd.isna(r[7]))
    return {"pulled": len(df), "saved": len(df), "start": end_date,
            "failed_dates": [], "expected_days": None, "actual_days": None}


def _sync_etf_daily(cfg: dict, end_date: str, backfill_from: str | None = None,
                   progress_cb: Callable | None = None) -> dict:
    """ETF日线同步（按日期批量拉取）。"""
    adapter = _get_kline_adapter(cfg)
    if backfill_from:
        start = backfill_from
    else:
        last = cfg.get("last_sync_date") or (date.today() - timedelta(days=30)).strftime("%Y%m%d")
        start = (pd.Timestamp(last) + timedelta(days=1)).strftime("%Y%m%d")
        if start > end_date:
            return {"pulled": 0, "saved": 0, "start": last, "failed_dates": [], "expected_days": 0, "actual_days": 0}

    r = _sync_by_trade_date(
        lambda trade_date: adapter.pull_daily_batch(trade_date, "etf"),
        lambda df: _daily_to_save_fn(df, adapter),
        start, end_date, api_name=_api_name_of(cfg), progress_cb=progress_cb,
        provider=adapter.provider)
    r["start"] = start
    return r


def _sync_etf_list(cfg: dict, end_date: str, backfill_from: str | None = None,
                   progress_cb: Callable | None = None) -> dict:
    """ETF基金列表全量同步。"""
    pro = _get_pro()
    from src.data_platform.rate_limit import rate_limit_context
    with rate_limit_context(_get_rate_ds("tushare"), "fund_basic"):   # 批 64b 裸调收编（档 0.3s）
        df = pro.fund_basic(market="E")   # DB 优化：拉取在事务外
    rows = [(r.get("ts_code"), r.get("name"), r.get("management"),
             r.get("fund_type"), r.get("invest_type"), str(r.get("list_date", "")))
            for r in df.to_dict("records")]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO etf_basic_info (ts_code, name, management, fund_type, invest_type, list_date)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ts_code) DO UPDATE SET name=EXCLUDED.name, management=EXCLUDED.management
            """, rows)
        conn.commit()
    _sm_upsert(((vt := _ts_to_vt_prefix(r[0])), "astock",
                (vt.rsplit(".", 1) + [""])[1], "etf", None, r[1], None,
                100, 0.001, "T+1", r[5], None)
               for r in rows if r[0])
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
    逐只 _pull_minute（内部分段，处理 stk_mins 8000 条限制）+ _save_minute（DB 写在熔断上下文外）。
    """
    sync_id = cfg["id"]
    adapter = _get_kline_adapter(cfg)
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

    from src.data_platform.rate_limit import rate_limit_context
    ds = _get_rate_ds(adapter.provider)
    ts_codes = _list_static_ts_codes("astock")
    total = len(ts_codes)
    total_pulled = 0
    total_saved = 0
    failed: list[str] = []
    for i, tc in enumerate(ts_codes, 1):
        try:
            # 限速（限流治理吸收）：节奏档取 daily（原 0.15s 硬编码→三级可调）
            # 归因拆分（2026-09-03）：熔断上下文只包 Tushare 拉取，DB 写在外——
            # DB 写失败计入 failed 不误伤熔断器（否则 DB 抖动打穿 Tushare 配额熔断）
            with rate_limit_context(ds, "daily"):
                df = _pull_minute(adapter, tc, freq, start, end_date)
            if df is not None and not df.empty:
                total_saved += _save_minute(adapter, df, freq)
                total_pulled += len(df)
        except Exception as ex:
            failed.append(f"{tc}:{type(ex).__name__}:{str(ex)[:40]}")
        if progress_cb:
            progress_cb(i, total, tc)

    return {"pulled": total_pulled, "saved": total_saved, "start": start,
            "failed_dates": failed, "expected_days": None,
            "actual_days": total - len(failed)}


# --- 工具函数 ---

def _adj_map_for_df(df: pd.DataFrame, adapter) -> dict:
    """当日全市场复权因子 {ts_code: factor}。**降级返回 {}——同步继续，因子 NULL**
    （A/B-F1 契约：积分未到账不阻塞日线同步；到账后回补 adj_factor 即恢复）。
    adapter 提供 pull_adj_factor（Tushare 拉因子，其他源返回空）。
    批 58 补限速（用户裁定）：adj_factor 接口有 Tushare 侧速率限制，连续 backfill 无 sleep 会
    触发限流返回空→因子 NULL（旧路径限速遗漏——backfill_adj_factor 用 adj_factor 档此处未用）。
    """
    try:
        td = str(df["trade_date"].iloc[0])
        from src.data_platform.rate_limit import rate_limit_context
        with rate_limit_context(_get_rate_ds(adapter.provider), "adj_factor"):
            fdf = adapter.pull_adj_factor(trade_date=td)
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


def _daily_to_save_fn(df: pd.DataFrame, adapter) -> int:
    """daily DataFrame -> 行 -> 入库（_sync_by_trade_date 的 save_fn 适配，24 号 adapter 化）。"""
    adj_map = _adj_map_for_df(df, adapter)
    return _save_bars(adapter.to_bar_rows(df, "1D", adj_map))


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
    from src.data_platform.schema import to_vt_symbol
    from src.data_platform.data_source import get_data_source, TushareDataSource
    from src.data_platform.rate_limit import rate_limit_context
    ds = get_data_source("tushare") or TushareDataSource()
    adapter = _get_kline_adapter({})   # 复权因子默认 tushare

    with get_conn() as conn:
        sql = ("SELECT DISTINCT (ts AT TIME ZONE 'Asia/Shanghai')::date FROM bar_1d WHERE adj_factor IS NULL "
               # 只扫股票行：asset_static_info 是 A 股静态表（ETF/转债不在其中）
               "AND symbol IN (SELECT DISTINCT REPLACE(REPLACE(REPLACE(ts_code,'.SH','.SHSE'),"
               "'.SZ','.SZSE'),'.BJ','.BSE') FROM asset_static_info)")
        params: list = []
        if start_date:
            sql += " AND (ts AT TIME ZONE 'Asia/Shanghai')::date >= %s"
            params.append(start_date)
        if end_date:
            sql += " AND (ts AT TIME ZONE 'Asia/Shanghai')::date <= %s"
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
        # 限速（限流治理吸收）：原 sleep(0.3) → 三级可调（默认档同为 0.3s）
        with rate_limit_context(ds, "adj_factor"):
            fdf = adapter.pull_adj_factor(trade_date=td)
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
    return {"status": "success", "days": len(dates), "processed": done, "updated": updated}


def _sync_index_daily(cfg: dict, end_date: str, backfill_from: str | None = None,
                      progress_cb: Callable | None = None) -> dict:
    """指数日线同步 handler（ptrade 批 1，接入 sync_config 体系）。

    固定拉 000300.SH 沪深300（可配基准列表留 system_config.benchmark_symbols 后续）。
    返回无 last_success_date 键 → sync() 走无条件推进（分钟线同款，防游标冻死）。
    """
    start = backfill_from or "20050408"
    r = sync_benchmark_index("000300.SH", start=start, end=end_date)
    return {"pulled": r.get("pulled", 0), "saved": r.get("saved", 0), "start": start,
            "failed_dates": [] if r.get("status") == "success" else [f"index_daily:{r.get('status')}"],
            "expected_days": None, "actual_days": None}


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
    "index_daily": _sync_index_daily,
}


# ═══ 批 58·M3 通用引擎循环（bar 族收编——fetch(kind, sub_kind) 统一契约，灰度切）═══

def _read_sync_kind(sync_id: str) -> dict:
    """读 sync_kind_config 归置行（kind/sub_kind/pg_table/rebuild）。无行/表缺返回 {}。"""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT kind, sub_kind, pg_table, rebuild FROM sync_kind_config WHERE sync_id=%s",
                (sync_id,))
            row = cur.fetchone()
    except psycopg.errors.UndefinedTable:
        return {}
    if not row:
        return {}
    return {"kind": row[0], "sub_kind": row[1], "pg_table": row[2], "rebuild": row[3]}


def _fetch_supply(adapter, *, kind: str, sub_kind: str | None, symbols: tuple[str, ...],
                  start: str, end: str, freq: str, preserve: bool = True):
    """构造 supply DataRequest → 直接 adapter.fetch（决策 4：不走 routing.resolve）。"""
    from datetime import datetime as _dt
    from src.quant_common.contract import DataRequest
    rng = (_dt.strptime(start, "%Y%m%d"), _dt.strptime(end, "%Y%m%d"))
    return adapter.fetch(DataRequest(
        kind=kind, symbols=symbols, temporality="historical", sub_kind=sub_kind,
        range_=rng, freq=freq, consumer_tag="sync", mode="supply",
        preserve_current_normalization=preserve))


def _sync_via_kind_daily_batch(adapter, *, sub: str, cfg: dict, start: str, end_date: str,
                               progress_cb: Callable | None = None) -> dict:
    """bar_daily+stock/etf 逐日批：镜像 _sync_by_trade_date 语义（fetch 替代 pro_api_fn+save_fn）。

    逐日 fetch(bar_daily, sub, range_=(day,day)) → _save_bars（DB 写在熔断上下文外，保持归因拆分）；
    failed_dates/last_success_date（连续成功末）/expected_days 与 _sync_by_trade_date 一致。
    返回含 last_success_date 键 → sync() 走三态游标。
    """
    from src.data_platform.rate_limit import rate_limit_context
    ds = _get_rate_ds(adapter.provider)
    api_name = _api_name_of(cfg)
    date_range = pd.date_range(start=start, end=end_date, freq="B")
    total = len(date_range)
    total_pulled = 0
    total_saved = 0
    failed_dates: list[str] = []
    last_success_date: str | None = None
    broken = False

    for i, d in enumerate(date_range, 1):
        trade_date = d.strftime("%Y%m%d")
        try:
            with rate_limit_context(ds, api_name):
                frame = _fetch_supply(adapter, kind="bar_daily", sub_kind=sub,
                                      symbols=(), start=trade_date, end=trade_date, freq="1D")
            if frame.rows:
                total_saved += _save_bars(list(frame.rows))
                total_pulled += len(frame.rows)
        except Exception as e:
            failed_dates.append(f"{trade_date}:{type(e).__name__}:{str(e)[:40]}")
            broken = True
        else:
            if not broken:
                last_success_date = trade_date
        if progress_cb:
            progress_cb(i, total, trade_date)

    return {
        "pulled": total_pulled,
        "saved": total_saved,
        "failed_dates": failed_dates,
        "expected_days": _expected_trading_days(start, end_date),
        "actual_days": len(date_range) - len(failed_dates),
        "last_success_date": last_success_date,
        "start": start,
    }


def _sync_via_kind_cb_daily(adapter, *, start: str, end_date: str,
                            progress_cb: Callable | None = None) -> dict:
    """bar_daily+convertible 区间：镜像 _sync_cb_daily——单次 fetch → _save_bars；无 last_success_date。"""
    frame = _fetch_supply(adapter, kind="bar_daily", sub_kind="convertible",
                          symbols=(), start=start, end=end_date, freq="1D")
    if not frame.rows:
        return {"pulled": 0, "saved": 0, "start": start, "failed_dates": [],
                "expected_days": 0, "actual_days": 0}
    return {"pulled": len(frame.rows), "saved": _save_bars(list(frame.rows)), "start": start,
            "failed_dates": [], "expected_days": None, "actual_days": None}


def _sync_via_kind_index(adapter, *, start: str, end_date: str,
                         progress_cb: Callable | None = None) -> dict:
    """index_daily 区间：镜像 _sync_index_daily（000300.SH）——单次 fetch → save_index_bars；无 last_success_date。"""
    from src.data_platform.db import save_index_bars
    frame = _fetch_supply(adapter, kind="index_daily", symbols=("000300.SH",),
                          start=start, end=end_date, freq="1D")
    saved = save_index_bars(list(frame.rows)) if frame.rows else 0
    return {"pulled": len(frame.rows), "saved": saved, "start": start,
            "failed_dates": [] if frame.rows else ["index_daily:empty"],
            "expected_days": None, "actual_days": None}


def _sync_via_kind_minute(adapter, *, sync_id: str, start: str, end_date: str,
                          progress_cb: Callable | None = None) -> dict:
    """bar_minute per-symbol：镜像 _sync_astock_minute——逐只 fetch（分段在 adapter 内）→ save_bars(freq)。

    DB 写在 rate_limit_context 外（归因拆分，同 _sync_astock_minute）；无 last_success_date。
    """
    from src.data_platform.rate_limit import rate_limit_context
    from src.data_platform.db import save_bars
    ds = _get_rate_ds(adapter.provider)
    freq = _MINUTE_FREQ.get(sync_id)
    if freq is None:
        return {"pulled": 0, "saved": 0, "start": end_date, "failed_dates": [],
                "expected_days": None, "actual_days": None}
    preserve = _preserve_normalization()   # 批 58b：竞价条并入开关（默认 True 保留现行，循环外读一次）
    ts_codes = _list_static_ts_codes("astock")
    total = len(ts_codes)
    total_pulled = 0
    total_saved = 0
    failed: list[str] = []
    for i, tc in enumerate(ts_codes, 1):
        try:
            with rate_limit_context(ds, "daily"):
                frame = _fetch_supply(adapter, kind="bar_minute", symbols=(tc,),
                                      start=start, end=end_date, freq=freq, preserve=preserve)
            if frame.rows:
                total_saved += save_bars(freq, list(frame.rows))
                total_pulled += len(frame.rows)
        except Exception as ex:
            failed.append(f"{tc}:{type(ex).__name__}:{str(ex)[:40]}")
        if progress_cb:
            progress_cb(i, total, tc)
    return {"pulled": total_pulled, "saved": total_saved, "start": start,
            "failed_dates": failed, "expected_days": None,
            "actual_days": total - len(failed)}


def _sync_via_kind(cfg: dict, end_date: str, backfill_from: str | None = None,
                   progress_cb: Callable | None = None) -> dict:
    """批 58·M3 通用引擎循环：读 sync_kind_config 归置行 → 按 (kind, sub_kind) 定粒度 → fetch → 落仓。

    签名与 _HANDLERS handler 同型（sync() 游标逻辑复用）。本步只收编 bar 族 6 sync_id（决策 4），
    非 bar 族 kind 抛 UnsupportedFeature 兜底（白名单手动控制只含 bar 族）。
    """
    from src.data_platform.adapters.base import UnsupportedFeature
    sync_id = cfg["id"]
    row = _read_sync_kind(sync_id)
    if not row:
        # 必须 raise（不能 return error status）——sync() 不查 r.get("status")，返回 error 会被当
        # 成功并无条件推进游标跳过整个窗口（数据丢失）。raise 让 sync() 外层 except 收编→error+不动游标。
        raise RuntimeError(f"sync_kind_config 无归置行: {sync_id}（迁移 0094 未上产或白名单配错）")
    kind = row["kind"]
    sub = row["sub_kind"]
    # index_daily 走 sync_benchmark_index 同款 adapter 选择（_get_kline_adapter({})——指数非 K 线
    # 数据面，绕开 M2 试点 resolve 选源，否则 routing_kline_pilot=on 时会误路由到缺能力源）
    adapter = _get_kline_adapter({} if kind == "index_daily" else cfg)

    if backfill_from:
        start = backfill_from
    elif kind == "index_daily":
        start = "20050408"   # 基准指数全量起点（同 _sync_index_daily）
    else:
        default_days = 7 if kind == "bar_minute" else 30
        last = cfg.get("last_sync_date") or (date.today() - timedelta(days=default_days)).strftime("%Y%m%d")
        start = (pd.Timestamp(last) + timedelta(days=1)).strftime("%Y%m%d")
        if start > end_date:
            return {"pulled": 0, "saved": 0, "start": last, "failed_dates": [],
                    "expected_days": 0, "actual_days": 0}

    if kind == "bar_daily" and sub in ("stock", "etf"):
        return _sync_via_kind_daily_batch(adapter, sub=sub, cfg=cfg, start=start,
                                          end_date=end_date, progress_cb=progress_cb)
    if kind == "bar_daily" and sub == "convertible":
        return _sync_via_kind_cb_daily(adapter, start=start, end_date=end_date, progress_cb=progress_cb)
    if kind == "index_daily":
        return _sync_via_kind_index(adapter, start=start, end_date=end_date, progress_cb=progress_cb)
    if kind == "bar_minute":
        return _sync_via_kind_minute(adapter, sync_id=sync_id, start=start, end_date=end_date,
                                     progress_cb=progress_cb)
    raise UnsupportedFeature(f"通用引擎未实现 kind={kind}, sub_kind={sub}（bar 族外后续切）")


# ═══ 三档数据第一档：全局定时同步 handler（U 审 2026-08-19）═══
# 通用模式：pull(trade_date) → DataFrame → 逐行 upsert 到专用表
# soft_time_limit 由 celery task 侧覆盖（≥600s），此处只做数据层

def _make_tier1_handler(table: str, pull_fn_name: str, pk_cols: list[str],
                        float_cols: list[str] | None = None, text_cols: list[str] | None = None):
    """工厂：生成第一档按日批量同步 handler。

    Args:
        table: 目标表名（如 'stk_limit'）
        pull_fn_name: tushare_adapter 里的 pull 函数名（如 'pull_stk_limit'）
        pk_cols: 主键列（用于 ON CONFLICT）
        float_cols: NUMERIC 列名列表
        text_cols: TEXT 列名列表
    """
    import importlib
    adapter = importlib.import_module("src.data_platform.adapters.tushare_adapter")
    pull_fn = getattr(adapter, pull_fn_name)
    all_cols = (float_cols or []) + (text_cols or [])
    conflict = ", ".join(pk_cols)
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in all_cols if c not in pk_cols)

    def _handler(cfg: dict, end_date: str, backfill_from: str | None = None,
                 progress_cb=None) -> dict:
        """通用第一档同步：按 trade_date 拉全市场 → upsert。"""
        from src.data_platform.db import get_conn as _gc
        from src.data_platform.data_source import get_data_source, TushareDataSource
        from src.data_platform.rate_limit import rate_limit_context
        ds = get_data_source("tushare") or TushareDataSource()
        # 修 2026-08-19：backfill_from 直接用（含当日）；增量才 +1 天（last_sync_date 的次日）
        if backfill_from:
            start_ts = backfill_from
        else:
            _last = cfg.get("last_sync_date") or (date.today() - timedelta(days=3)).strftime("%Y%m%d")
            start_ts = (pd.Timestamp(_last) + timedelta(days=1)).strftime("%Y%m%d")
        if start_ts > end_date:
            return {"pulled": 0, "saved": 0, "start": start_ts, "failed_dates": [], "expected_days": 0, "actual_days": 0}

        date_range = pd.date_range(start=start_ts, end=end_date, freq="B")
        total_pulled = total_saved = 0
        failed_dates = []
        for d in date_range:
            td = d.strftime("%Y%m%d")
            try:
                # forecast 按 ann_date 拉（公告日驱动），其余按 trade_date；
                # 限速（限流治理吸收）：原 0.3s 硬编码 → rate_limit_context 三级可调
                # 批 64b：档键从 "daily" 改 per-API（table==api 名，DEFAULT_RATE_LIMITS 各键 0.3s）
                with rate_limit_context(ds, table):
                    if "ann_date" in pull_fn.__code__.co_varnames:
                        df = pull_fn(ann_date=td)
                    else:
                        df = pull_fn(trade_date=td)
                if df is not None and not df.empty:
                    # 修 2026-08-19：insert_cols 含 PK，placeholders 必须同长（原漏 PK 导致每日 INSERT 失败）
                    insert_cols = pk_cols + [c for c in all_cols if c not in pk_cols]
                    placeholders = ", ".join(["%s"] * len(insert_cols))
                    cols_sql = ", ".join(insert_cols)
                    upsert = (f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders}) "
                              f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}" if updates else
                              f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders}) "
                              f"ON CONFLICT ({conflict}) DO NOTHING")
                    with _gc() as conn:
                        with conn.cursor() as cur:
                            # DB 优化（2026-08-21 盘点）：逐行 execute（单日全市场 ~5000 次往返）
                            # → executemany 一次提交（psycopg3 pipeline，db.py save_bars 同款范式）
                            batch = []
                            for row in df.to_dict("records"):
                                vals = []
                                for c in insert_cols:
                                    v = row.get(c)
                                    if c in (float_cols or []):
                                        vals.append(adapter._safe_float(v) if v is not None else None)
                                    else:
                                        vals.append(str(v) if v is not None else None)
                                batch.append(tuple(vals))
                            cur.executemany(upsert, batch)
                        conn.commit()
                    total_pulled += len(df)
                    total_saved += len(df)
            except Exception as e:
                failed_dates.append(f"{td}:{type(e).__name__}:{str(e)[:40]}")
            if progress_cb:
                progress_cb(len(date_range), len(date_range), td)
        return {"pulled": total_pulled, "saved": total_saved, "start": start_ts,
                "failed_dates": failed_dates,
                "expected_days": len(date_range), "actual_days": len(date_range) - len(failed_dates)}

    return _handler


def _make_full_rebuild_handler(table: str, pull_fn_name: str, pk_cols: list[str],
                              text_cols: list[str]):
    """工厂：生成全量重建 handler（每周一跑，DELETE 全表后 INSERT）。

    批 56a·M1：重建后若表=namechange，追加派生 security_state(st) 时变行——
    ST 状态从曾用名推断（当前有效名含 ST→is_st=true，start_date=生效日，
    29 号 §四六源之一：st←namechange_sync，含 start_date 天然 PIT）。
    """
    import importlib
    adapter = importlib.import_module("src.data_platform.adapters.tushare_adapter")
    pull_fn = getattr(adapter, pull_fn_name)

    def _handler(cfg: dict, end_date: str, backfill_from: str | None = None,
                 progress_cb=None) -> dict:
        from src.data_platform.db import get_conn as _gc
        from src.data_platform.rate_limit import rate_limit_context
        with rate_limit_context(_get_rate_ds("tushare"), table):   # 批 64b 裸调收编（namechange/concept，档 0.3s）
            df = pull_fn(trade_date=end_date) if "trade_date" in pull_fn.__code__.co_varnames else pull_fn()
        if df is None or df.empty:
            return {"pulled": 0, "saved": 0, "start": "", "failed_dates": ["空数据"], "expected_days": 1, "actual_days": 0}
        cols = list(df.columns)
        placeholders = ", ".join(["%s"] * len(cols))
        cols_sql = ", ".join(cols)
        with _gc() as conn:
            # DB 优化（2026-08-21 盘点）：DELETE+逐行 INSERT 单事务（表随周增长持锁越长）
            # → executemany 一次提交（缩短 DELETE→INSERT 间的锁窗口）
            conn.execute(f"DELETE FROM {table}")
            with conn.cursor() as cur:
                batch = [tuple(str(row[c]) if row[c] is not None else None for c in cols)
                         for row in df.to_dict("records")]
                cur.executemany(f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders})", batch)
            conn.commit()
        if table == "namechange":
            _derive_st_states(df)
        return {"pulled": len(df), "saved": len(df), "start": "full",
                "failed_dates": [], "expected_days": 1, "actual_days": 1}

    return _handler


def _derive_st_states(df) -> None:
    """从 namechange 全表派生 ST 时变行（fail-soft）。

    规则：每只股票的每个曾用名区间一行；name 含 'ST'→is_st=true。
    脏值防御（盲审 B）：ts_code/name/start_date 为 NaN 等非字符串跳行；
    历史区间（end_date 非空）缺 start_date 跳行——防伪行以 today 为 effective_from
    遮蔽现行 ST 状态；现行名（end_date 空）缺 start 落 today（现行状态不丢）。
    """
    import json as _json
    from datetime import date as _d
    try:
        rows = []
        today = _d.today().isoformat()
        for r in df.to_dict("records"):
            ts = r.get("ts_code")
            name = r.get("name")
            name = name if isinstance(name, str) else ""       # NaN 真值——isinstance 挡
            if not isinstance(ts, str) or not ts or not name:
                continue
            start = r.get("start_date")
            start = start.strip() if isinstance(start, str) else ""
            end = r.get("end_date")
            end = end.strip() if isinstance(end, str) else ""
            if start.isdigit() and len(start) == 8:
                eff = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
            elif not end:
                eff = today
            else:
                continue
            rows.append((_ts_to_vt_prefix(ts), eff, "st",
                         _json.dumps({"name": name, "is_st": "ST" in name.upper()})))
        _sm_upsert_state(rows)
        logger.info("security_state(st) 派生 %d 行（namechange 重建）", len(rows))
    except Exception as e:
        logger.warning("security_state(st) 派生失败（不影响 namechange 同步）: %s", e, exc_info=True)


# 注册 9 个 handler（按迁移 0045 表结构）
_TIER1_FLOAT_COLS = {
    "stk_limit": ["pre_close", "up_limit", "down_limit"],
    "moneyflow": ["buy_sm_vol","buy_sm_amount","sell_sm_vol","sell_sm_amount",
                   "buy_md_vol","buy_md_amount","sell_md_vol","sell_md_amount",
                   "buy_lg_vol","buy_lg_amount","sell_lg_vol","sell_lg_amount",
                   "buy_elg_vol","buy_elg_amount","sell_elg_vol","sell_elg_amount",
                   "net_mf_vol","net_mf_amount"],
    "margin_detail": ["rzye","rqye","rzmre","rqyl","rzche","rqchl","rqmcl","rzrqye"],
    "top_list": ["close","pct_change","turnover_rate","amount","l_sell","l_buy","l_amount","net_amount","net_rate","amount_rate","float_values"],
    "block_trade": ["price","vol","amount"],
    "cyq_perf": ["his_low","his_high","cost_5pct","cost_15pct","cost_50pct","cost_85pct","cost_95pct","weight_avg","winner_rate"],
    "forecast": ["p_change_min","p_change_max","net_profit_min","net_profit_max","last_parent_net"],
}
_TIER1_TEXT_COLS = {
    "stk_limit": [],
    "moneyflow": [],
    "margin_detail": [],
    "top_list": ["name","reason"],
    "block_trade": ["buyer","seller"],
    "cyq_perf": [],
    "forecast": ["type","summary","change_reason","first_ann_date"],
    "namechange": ["name","start_date","end_date","ann_date","change_reason"],
    "concept": ["name"],
}

# 按日批量的（7 个）
_TIER1_BATCH = {
    "stk_limit_sync":     ("stk_limit",     "pull_stk_limit",     ["trade_date","ts_code"]),
    "moneyflow_sync":     ("moneyflow",     "pull_moneyflow",     ["ts_code","trade_date"]),
    "margin_detail_sync": ("margin_detail", "pull_margin_detail", ["trade_date","ts_code"]),
    "top_list_sync":      ("top_list",      "pull_top_list",      ["trade_date","ts_code"]),
    "block_trade_sync":   ("block_trade",   "pull_block_trade",   ["ts_code","trade_date"]),
    "cyq_perf_sync":      ("cyq_perf",      "pull_cyq_perf",      ["ts_code","trade_date"]),
    "forecast_sync":      ("forecast",      "pull_forecast",      ["ts_code","ann_date","end_date"]),
}
for _sid, (_tbl, _pull, _pk) in _TIER1_BATCH.items():
    _HANDLERS[_sid] = _make_tier1_handler(
        _tbl, _pull, _pk,
        float_cols=_TIER1_FLOAT_COLS.get(_tbl, []),
        text_cols=_TIER1_TEXT_COLS.get(_tbl, []))

# 全量重建的（2 个）
_TIER1_FULL = {
    "namechange_sync": ("namechange", "pull_namechange", ["ts_code","name","start_date"]),
    "concept_sync":    ("concept",    "pull_concept",    ["ts_code"]),
}
for _sid, (_tbl, _pull, _pk) in _TIER1_FULL.items():
    _HANDLERS[_sid] = _make_full_rebuild_handler(
        _tbl, _pull, _pk, text_cols=_TIER1_TEXT_COLS.get(_tbl, []))



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

# 各 sync_id 对应的：tushare 拉取 API / 静态信息表 / ts_code 来源
# astock_daily -> pro.daily(ts_code=) / asset_static_info
# etf_daily    -> pro.fund_daily(ts_code=) / etf_basic_info
# cb_daily     -> pro.cb_daily(ts_code=) / cb_basic_info（注：cb_daily 按日期拉全量更高效，per-symbol 仍支持）
# astock_minute/_5min -> pro.stk_mins(ts_code=,freq=) / asset_static_info（per-symbol only，不支持按日全市场）

_TUSHARE_MIN_DATE = os.environ.get("SYNC_START_DATE", "20100101")  # 全量起点，.env 可配（默认 2010）


def _get_pro_api(sync_id: str):
    """返回 (adapter, kind, freq, bar_type)（24 号 adapter 化，替代原 pro/api_fn）。

    adapter 按 sync_config.provider 路由；kind/freq/bar_type 从 _PER_SYMBOL_META。
    不支持的 sync_id 返回 (adapter, None, None, None)。
    """
    adapter = _get_kline_adapter(_get_config(sync_id))
    meta = _PER_SYMBOL_META.get(sync_id)
    if meta is None:
        return adapter, None, None, None
    freq, _table, kind, bar_type = meta
    return adapter, kind, freq, bar_type


def _list_static_ts_codes(kind: str) -> list[str]:
    """从静态信息表取全部 ts_code（Tushare 格式）。

    盲审 B-P0：static_symbols 只写 A 股（股票，static_list_sync 仅 stock_basic 入库），
    对 kind=etf/cb 不能走快路径——会返回股票代码，delete_by_sync_item 删错标的（数据丢失）。
    astock 走 static_symbols 快路径（含退市标记），etf/cb 直查专用表。
    """
    if kind == "astock":
        # P3-14: 优先 static_symbols（含退市标记）
        try:
            with get_conn() as conn:
                cur = conn.execute("SELECT ts_code FROM static_symbols WHERE coalesce(delisted, false) = false ORDER BY ts_code")
                rows = cur.fetchall()
            if rows:
                return [r[0] for r in rows]
        except Exception as e:
            logger.warning("查询 static_symbols 失败: %s", e)
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
                f"SELECT DISTINCT to_char(ts AT TIME ZONE 'Asia/Shanghai', 'YYYYMMDD') FROM {table} "
                f"WHERE symbol=%s ORDER BY 1",
                (vt_symbol,))
            return [r[0] for r in cur.fetchall()]
    except psycopg.errors.UndefinedTable:
        return []


def _expected_trade_dates(start: str, end: str) -> list[str]:
    """算区间内预期 A 股交易日（YYYYMMDD 升序）。

    优先 trade_cal（DB 多年）-> pro.trade_cal 按年拉补 -> 回退工作日。
    （原 api_fn 形参未使用，24 号 adapter 化时删除。）
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


def _find_gaps(kind: str, ts_code: str, first: str, last: str,
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
    expected = _expected_trade_dates(scan_start, scan_end)
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


def _pull_daily_df(adapter, ts_code: str, start: str, end: str, kind: str = "astock") -> "pd.DataFrame | None":
    """日线拉取（只 API，异常透传）。返回 df；None/空/缺 trade_date 列返回对应值。

    归因拆分（2026-09-03）：与 _fetch_and_save 的差异——不吞 API 异常、不写库，
    供 sync_all 在 rate_limit_context 内只包数据源调用用（API 失败计熔断，DB 写在外）。
    """
    df = adapter.pull_daily(ts_code, start, end, kind=kind)
    if df is None or df.empty:
        return df
    if "trade_date" not in df.columns:
        return None
    return df


def _fetch_and_save(adapter, ts_code: str, start: str, end: str, save_fn,
                    kind: str = "astock") -> "pd.DataFrame | None":
    """拉取一段日期数据并入库（24 号 adapter 化）。返回 df（供调用方统计）。

    因子语义（盲审 A-P1-3/B-P1-2 修复）：per-symbol 多日路径**不传 adj_map**——因子用
    df 自身 adj_factor（pro_bar adj=None 时全 NULL），靠 backfill_adj_factor 回填正确因子。
    原 _adj_map_for_df 取首日因子应用到多日全区间，是「首日因子污染多日」的错值源。
    """
    try:
        df = adapter.pull_daily(ts_code, start, end, kind=kind)
    except Exception:
        return None
    if df is None or df.empty:
        return df
    if "trade_date" not in df.columns:
        return None
    rows = adapter.to_bar_rows(df, "1D", None)
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


def _pull_minute(adapter, ts_code: str, freq: str, start: str, end: str) -> "pd.DataFrame | None":
    """分钟线分段拉取（只 API，不写库）。stk_mins per-symbol + 8000 条分段。

    每段单独调 pull_minute（按 09:00~15:00 交易时段），避免单次返回被截断丢数据。
    归因拆分（2026-09-03）：异常透传——数据源调用失败需被 rate_limit_context 计入熔断，
    故与 DB 写拆开（DB 写失败不应打穿数据源配额熔断）。
    """
    from src.data_platform.adapters.tushare_adapter import split_minute_range
    total_df: list[pd.DataFrame] = []
    for s, e in split_minute_range(start, end, freq):
        df = adapter.pull_minute(ts_code, freq, f"{s} 09:00:00", f"{e} 15:00:00")
        if df is None or df.empty:
            continue
        total_df.append(df)
    if not total_df:
        return None
    return pd.concat(total_df, ignore_index=True)


def _save_minute(adapter, df: pd.DataFrame, freq: str, overwrite: bool = False) -> int:
    """分钟线入库（只写库）。overwrite=True 覆盖写（回补），False 冲突跳过（增量/全量）。

    归因拆分（2026-09-03）：调用方把它放 rate_limit_context 外，DB 写失败计入 failed
    而非数据源熔断。
    """
    from src.data_platform.db import save_bars, save_bars_overwrite
    save_fn = save_bars_overwrite if overwrite else save_bars
    rows = adapter.to_bar_rows(df, freq, None)
    return save_fn(freq, rows)


def _fetch_minute_and_save(adapter, ts_code: str, freq: str, start: str, end: str,
                           overwrite: bool = False) -> "tuple[pd.DataFrame | None, int]":
    """拉取+入库（组合 _pull_minute + _save_minute，per-symbol 路径用）。

    返回 (concat_df, saved)。overwrite=True 覆盖写（回补），False 冲突跳过（增量/全量）。
    """
    df = _pull_minute(adapter, ts_code, freq, start, end)
    if df is None or df.empty:
        return None, 0
    saved = _save_minute(adapter, df, freq, overwrite)
    return df, saved


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
    adapter, kind, freq, bar_type = _get_pro_api(sync_id)
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
                df, _saved = _fetch_minute_and_save(adapter, ts_code, freq, start, today)
                return _wrap_minute_result(df, "full", cnt, start, today)
            gaps = _find_gaps(kind, ts_code, first, last, table=table)
            total_pulled = 0
            gap_ranges = []
            for g_start, g_end in gaps:
                df, _s = _fetch_minute_and_save(adapter, ts_code, freq, g_start, g_end)
                if df is not None and not df.empty:
                    total_pulled += len(df)
                    gap_ranges.append([g_start, g_end])
            return {"status": "uptodate" if not gap_ranges else "success",
                    "pulled": total_pulled, "saved": total_pulled,
                    "range": [first, last], "mode_used": "incremental",
                    "gaps_filled": gap_ranges, "local_count_before": cnt}
        elif mode == "full":
            start = _get_list_date(kind, ts_code)
            df, _saved = _fetch_minute_and_save(adapter, ts_code, freq, start, today)
            return _wrap_minute_result(df, "full", cnt, start, today)
        return {"status": "error", "error": f"未知 mode: {mode}"}

    # 日线分支（原逻辑）
    if mode == "auto":
        if cnt == 0:
            # 空 -> 从上市日起全量
            start = _get_list_date(kind, ts_code)
            used = "full"
            df = _fetch_and_save(adapter, ts_code, start, today, _save_bars, kind=kind)
            return _wrap_result(df, used, cnt, start, today)
        else:
            # 有数据 -> 完整性扫描：找出上市日到今天所有缺口段，逐段补
            gaps = _find_gaps(kind, ts_code, first, last)
            total_pulled = 0
            total_saved = 0
            gap_ranges = []
            for g_start, g_end in gaps:
                df = _fetch_and_save(adapter, ts_code, g_start, g_end, _save_bars, kind=kind)
                if df is not None and not df.empty:
                    total_pulled += len(df)
                    total_saved += len(df)
                    gap_ranges.append([g_start, g_end])
            used = "incremental"
            return {"status": "uptodate" if not gap_ranges else "success",
                    "pulled": total_pulled, "saved": total_saved,
                    "range": [first, last], "mode_used": used,
                    "gaps_filled": gap_ranges, "local_count_before": cnt}
    elif mode == "full":
        start = _get_list_date(kind, ts_code)
        used = "full"
        df = _fetch_and_save(adapter, ts_code, start, today, _save_bars, kind=kind)
        return _wrap_result(df, used, cnt, start, today)
    else:
        return {"status": "error", "error": f"未知 mode: {mode}"}


def backfill_symbol(sync_id: str, ts_code: str, start: str, end: str) -> dict:
    """单标的回补：用户指定范围重新下载，覆盖本地已有（DO UPDATE）。

    体现手动回补优先级高于增量：本地已有数据也用拉取的新值覆盖。
    """
    from src.data_platform.db import save_bars_overwrite
    adapter, kind, freq, bar_type = _get_pro_api(sync_id)
    if kind is None:
        return {"status": "error", "error": f"不支持 per-symbol 回补: {sync_id}"}

    # 分钟线分支：分段 stk_mins + 覆盖写
    if bar_type == "minute":
        df, _saved = _fetch_minute_and_save(adapter, ts_code, freq, start, end, overwrite=True)
        if df is None or df.empty:
            return {"status": "empty", "pulled": 0, "saved": 0, "range": [start, end]}
        actual_first = str(df["trade_time"].min())[:10].replace("-", "")
        actual_last = str(df["trade_time"].max())[:10].replace("-", "")
        return {"status": "success", "pulled": len(df), "saved": len(df),
                "range": [actual_first, actual_last], "overwritten": True}

    # 日线分支（原逻辑）
    try:
        df = adapter.pull_daily(ts_code, start, end, kind=kind)
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {str(e)[:120]}"}

    if df is None or df.empty:
        return {"status": "empty", "pulled": 0, "saved": 0, "range": [start, end]}

    if "trade_date" not in df.columns:
        return {"status": "error", "error": f"响应缺 trade_date 列: {list(df.columns)[:4]}"}

    # F-F2：单标的回补也带因子——to_bar_rows 不传 adj_map 时全 NULL，叠加 overwrite 的
    # DO UPDATE 会把已回填的 adj_factor 清回 NULL（E/F 盲审实测的数据破坏路径）；
    # 拉不到因子（降级）时 COALESCE 兜底不清空（schema.py BAR_TABLE_INSERT_OVERWRITE）
    try:
        fdf = adapter.pull_adj_factor(symbol=ts_code, start=start, end=end)
        adj_map = (dict(zip(fdf["ts_code"], fdf["adj_factor"]))
                   if fdf is not None and not fdf.empty else {})
    except Exception:
        adj_map = {}
    rows = adapter.to_bar_rows(df, "1D", adj_map)
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


def delete_by_sync_item(sync_id: str, conn=None) -> dict:
    """全量删该同步项的本地数据 + 重置游标（切换 provider 用，24 号 §2.3 切换重建原语）。

    删 bar 表里该 kind 的所有标的（批量 SQL），并重置 last_sync_date 游标——
    调用方随后 full 回填。静态表空（无法确定删什么）返回 error 防静默假删。
    批27-23：conn 可选传入——传入则事务归调用方（switch-provider 原子化：改 provider+删数据
    一次 commit，防"删成功改失败=数据已丢未切换"与倒置的"新配置+旧数据混合"两种中间态）。
    """
    from src.data_platform.schema import to_vt_symbol
    meta = _PER_SYMBOL_META.get(sync_id)
    if meta is None:
        return {"status": "error", "error": f"不支持删除: {sync_id}"}
    table = meta[1]
    kind = meta[2]
    ts_codes = _list_static_ts_codes(kind)
    if not ts_codes:
        return {"status": "error", "error": f"静态表 {kind} 为空，无法确定删除范围"}   # 盲审 A-P2：防静默假删
    vts = [to_vt_symbol(tc) for tc in ts_codes]

    def _body(c) -> int:
        try:
            cur = c.execute(f"DELETE FROM {table} WHERE symbol = ANY(%s)", (vts,))
            deleted = cur.rowcount
        except psycopg.errors.UndefinedTable:
            deleted = 0
        c.execute("UPDATE sync_config SET last_sync_date=NULL, last_sync_ts=NULL, "
                  "last_sync_count=0, last_status='idle' WHERE id=%s", (sync_id,))   # 重置游标
        return deleted

    if conn is not None:
        return {"status": "success", "deleted": _body(conn)}   # 调用方事务（不 commit）
    with get_conn() as c:
        deleted = _body(c)
        c.commit()
    return {"status": "success", "deleted": deleted}


def sync_all(sync_id: str, progress_cb: Callable | None = None) -> dict:
    """全市场全量同步（Celery 调用）。

    遍历该类型全部标的，逐只强制 full（从上市日起全历史）。归因拆分（2026-09-03）：
    熔断上下文只包 Tushare 拉取（_pull_daily_df/_pull_minute），DB 写（_save_bars/_save_minute）
    在上下文外——不再经 sync_symbol（其 _fetch_and_save 吞 API 异常致归因反转）。
    progress_cb(i, total, ts_code) 写进度。
    """
    adapter, kind, freq, bar_type = _get_pro_api(sync_id)
    if kind is None:
        return {"status": "error", "error": f"不支持全量同步: {sync_id}"}
    from src.data_platform.rate_limit import rate_limit_context
    ds = _get_rate_ds(adapter.provider)

    ts_codes = _list_static_ts_codes(kind)
    total = len(ts_codes)
    today = date.today().strftime("%Y%m%d")
    ok = 0
    failed: list[str] = []
    total_saved = 0

    for i, tc in enumerate(ts_codes, 1):
        try:
            # 全量重建：强制 full（从上市日起全历史），不走 auto 完整性扫描
            # （auto 只补 first~last 缺口，不补上市日到 first 的早期缺口）；
            # 限速（限流治理吸收）：原 0.15s 硬编码 → rate_limit_context 三级可调（daily 档）
            # 归因拆分（2026-09-03）：熔断上下文只包 Tushare 拉取、DB 写在外。原
            # sync_symbol(mode="full") 把 _fetch_and_save（吞 API 异常）与 DB 写混进上下文：
            # API 失败被吞成 empty 假装成功、DB 写失败反而计熔断——归因完全反转，拆开根治。
            start = _get_list_date(kind, tc)
            if bar_type == "minute":
                with rate_limit_context(ds, "daily"):
                    df = _pull_minute(adapter, tc, freq, start, today)
                if df is not None and not df.empty:
                    total_saved += _save_minute(adapter, df, freq)
            else:
                with rate_limit_context(ds, "daily"):
                    df = _pull_daily_df(adapter, tc, start, today, kind=kind)
                if df is not None and not df.empty:
                    total_saved += _save_bars(adapter.to_bar_rows(df, "1D", None))
            ok += 1
        except Exception as e:
            failed.append(f"{tc}:{type(e).__name__}:{str(e)[:40]}")
        if progress_cb:
            progress_cb(i, total, tc)

    return {"status": "partial" if failed else "success",
            "total": total, "ok": ok, "failed_count": len(failed),
            "saved": total_saved, "failed": failed[:20]}


def list_symbols(sync_id: str, q: str = "", page: int = 1, size: int = 9999) -> dict:
    """列出某类型全部标的 + 本地数据状态（批量聚合查 bar_1D，避免逐只查）。

    Returns: {items:[{ts_code,name,list_date,local_count,local_first,local_last}], total}
    """
    from src.data_platform.schema import to_vt_symbol
    _, kind, _freq, _bar_type = _get_pro_api(sync_id)
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
            from src.data_platform.tz import as_shanghai
            for sym, cnt, mn, mx in cur.fetchall():
                # 批 56b 盲审 B：首末日=上海业务日（UTC 墙钟切片=早一天）
                local[sym] = (int(cnt),
                              as_shanghai(mn).strftime("%Y%m%d") if mn else None,
                              as_shanghai(mx).strftime("%Y%m%d") if mx else None)
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


def sync_benchmark_index(ts_code: str = "000300.SH", start: str = "20050408",
                         end: str | None = None) -> dict:
    """同步基准指数日线到 bar_index（ptrade 全家桶批 1，2026-09-04）。

    沪深300（000300.SH → 000300.SHSE）等指数独立存 bar_index，与股票 bar_1d 分离。
    幂等：ON CONFLICT (symbol, ts) DO NOTHING，重复同步跳过。
    """
    from src.data_platform.adapters.tushare_adapter import pull_index_daily
    from src.data_platform.db import save_index_bars
    adapter = _get_kline_adapter({})   # 基准指数默认 tushare（index_daily 专属）
    end = end or date.today().strftime("%Y%m%d")
    df = pull_index_daily(ts_code, start, end)
    if df.empty:
        return {"status": "empty", "pulled": 0, "saved": 0, "ts_code": ts_code}
    rows = adapter.to_bar_rows(df, "1D")
    saved = save_index_bars(rows)
    return {"status": "success", "pulled": len(df), "saved": saved, "ts_code": ts_code}
