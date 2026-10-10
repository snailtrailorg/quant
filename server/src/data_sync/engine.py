"""数据同步引擎 -- 通用增量/全量同步，按 sync_config.id 调度。

支持8种数据类型：astock_daily / astock_basic / astock_list /
cb_daily / cb_basic / etf_daily / etf_list / trade_cal

回补：sync(sync_id, backfill_from=YYYYMMDD) 回补历史缺口，不推进 last_sync_date 游标。
完整性：按日拉取的同步用 trade_cal 校验预期交易日 vs 实际成功日，缺口暴露在 sync_log。
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Callable

import pandas as pd
import psycopg
from dotenv import load_dotenv

from src.data_platform.db import get_conn, get_conn_for_heavy_read
from src.data_platform.security_master import normalize_board, sm_inception
from src.data_platform.jsonb import jsonb

load_dotenv()

logger = logging.getLogger("data_sync")

# 批 110·D：软时限异常的**窄捕获**用（per-symbol `except` 不吞它——见 `_reconcile_symbols`）。
# celery 缺席（纯数据层单测环境）⇒ `None` ⇒ 该分支恒不通（等价于「环境无软时限」）。
try:                                        # pragma: no cover - 环境相关
    from celery.exceptions import SoftTimeLimitExceeded as _CelerySoftTimeLimit
except Exception:                           # pragma: no cover - 环境相关
    _CelerySoftTimeLimit = None

_sync_log_table_created = False


def _provider_of(cfg: dict) -> str:
    """sync_config 行的 provider（批 83b 真路由唯一入口；缺省 tushare，与 0067 列默认一致）。"""
    return str((cfg or {}).get("provider") or "tushare")


def _get_pro(provider: str = "tushare"):
    """读 provider 对应数据源客户端（DB 优先，.env fallback；批 83b 从死钉 tushare 改读 provider）。

    非 bar 同步项（静态清单/日历/三档）的拉取函数在 tushare_adapter 内，按 P1-4 裁定
    **provider 恒 tushare**；此处读 provider 是让（a）限速/用量归属随配置走、
    （b）配置指向"半成品集成"时响亮失败而非静默按 tushare 拉数（防串源）。

    两种错误分域处理（批 83b 裁定，与 `_get_rate_ds`/`_get_kline_adapter` 三处口径一致）：
    - provider 已注册 adapter 但未注册 DataSource（半成品集成）→ `get_data_source` 抛
      `ProviderConfigError`，**直接穿透**（不吞、不回落——回落=把数据与用量记到 tushare 头上）。
    - provider 完全未知且未配库行（多为配置笔误）→ 维持盲审 A-P2/B-P2 fail-soft：
      告警 + 回落 tushare（"配置错 provider 不应打断同步"）。
    """
    from src.data_platform.data_source import FALLBACK_PROVIDER, fallback_data_source, get_data_source
    ds = get_data_source(provider)
    if ds is None:
        if provider != FALLBACK_PROVIDER:   # 兜底源自身无 DB 行=.env 合法路径，不该告警
            logger.warning("provider=%s 未注册 DataSource 且无 DB 配置行，回落兜底源 %s"
                           "（A-P2/B-P2 fail-soft）——存在串源风险，请核对 sync_config.provider",
                           provider, FALLBACK_PROVIDER)
        ds = fallback_data_source()
    ds.record_usage(provider=ds.provider, api_name="get_pro")
    return ds.get_client()


def _apply_exit_config(adapter):
    """批 102a：把该 adapter 的**出口配置**（代理 DSN / 端点覆盖）注入——**构造 adapter 时一次**。

    - 解析真源＝`data_platform/proxy.py`（`proxy_binding` + `proxy_config` 两表，迁移 0135）；
    - **禁进程级缓存**：每次构造都重新解析（写进程缓存会在测试打桩 DB 后污染后续用例）；
    - **只对 `supports_exit_config=True` 的 adapter 触库**：SDK 型（tushare/聚宽官方客户端不
      暴露 per-call 代理）不解析 ⇒ 既不让「无效配置」悄悄存在（写入侧已拒），
      也让 `test_engine_provider` 这类手搓 cfg 的单测**零 DB 耦合**；
    - **解析失败回落直连 + 响亮告警**（口径同本文件 `_routing_pilot_on`：配置表读不到＝
      回退现状路径）。出口代理是**增强**不是前置——一个可选配置的存储异常不该把同步任务
      整体打挂。**代价（明账）**：表缺/DB 抖动期代理不生效且直连可通时，表象与「正常直连」
      同形 ⇒ 靠本条 WARNING（journal 可查）+ `POST /api/proxies/{name}/probe` 实测兜底。
    """
    if not getattr(adapter, "supports_exit_config", False):
        return adapter
    from src.data_platform import proxy as proxy_store
    try:
        adapter.configure_exit(proxy=proxy_store.resolve_proxy(adapter.provider),
                               endpoint=proxy_store.resolve_endpoint(adapter.provider))
    except Exception as e:
        logger.warning("provider=%s 出口配置解析失败，本次回落直连（代理不生效）: %s: %s",
                       adapter.provider, type(e).__name__, e)
    return adapter


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
            from src.data_platform import routing
            from src.quant_common.contract import DataRequest
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


def _get_supply_adapter(cfg: dict):
    """供给面 adapter（批 100：按 `sync_config.provider` 选，未知回退 tushare + 告警）。

    tier1/全量重建等「非 bar 族」写入面同步项经此拿 adapter，再 `adapter.fetch_supply(kind, sub_kind, ...)`
    ——原 9 个 `importlib.import_module("…tushare_adapter")` 硬编码即在此收编。独立入口便于测试替身。
    """
    from src.data_platform.adapters.base import get_adapter
    provider = _provider_of(cfg)
    try:
        adapter = get_adapter(provider)
    except ValueError:
        logger.warning("未注册的数据源 provider=%s，回退 tushare", provider)
        adapter = get_adapter("tushare")
    return _apply_exit_config(adapter)


def _routing_pilot_on() -> bool:
    """试点开关（system_config 键；读失败=off——回退现状路径）。"""
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT value FROM system_config WHERE key='routing_kline_pilot'")
            r = cur.fetchone()
            return bool(r) and str(r[0]).lower() in ("on", "1", "true")
    except Exception:
        return False


# 批 72：_sync_kind_whitelist() 已退役删除（bar 族 6 键一步切 _sync_via_kind，灰度机制退役；
# system_config.sync_kind_routing 键失效——保留行仅供 revert 后旧代码语义自洽，
# 失效声明见模块契约 data_sync.md）


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
    """按 provider 选限速/熔断 DataSource（批 83b：get_data_source 抛穿透，不再静默换源）。

    盲审 A-P1-3：rate_limit_context 的 ds 原硬编码 tushare，切 provider 后限速/熔断串源——
    新源仍按 Tushare 间隔限速、熔断器 key=tushare。改为按 provider 选。

    `get_data_source` 的「半成品集成」→ `ProviderConfigError` **直接穿透**（本函数不捕获）；
    「provider 完全未知」→ None → 告警 + 回落兜底源（A-P2/B-P2 fail-soft，口径同 `_get_pro`）。
    兜底源自身无 DB 行 = 合法 .env fallback，不告警。
    """
    from src.data_platform.data_source import FALLBACK_PROVIDER, fallback_data_source, get_data_source
    ds = get_data_source(provider)
    if ds is None:
        if provider != FALLBACK_PROVIDER:
            logger.warning("provider=%s 无 DataSource 实例（未注册且无 DB 配置行），"
                           "限速/熔断/用量回落兜底源 %s（A-P2/B-P2 fail-soft）——存在串源风险",
                           provider, FALLBACK_PROVIDER)
        return fallback_data_source()
    return ds


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
        cur = conn.execute("SELECT id, name, tushare_api, pg_table, data_type, sync_mode, schedule, enabled, last_sync_date, last_sync_ts, last_status, provider, retention FROM sync_config WHERE id=%s", (sync_id,))
        row = cur.fetchone()
        if not row:
            return {}
        return {"id": row[0], "name": row[1], "api": row[2], "pg_table": row[3],
                "data_type": row[4], "mode": row[5], "schedule": row[6],
                "enabled": row[7], "last_sync_date": row[8],
                "last_sync_ts": row[9], "last_status": row[10], "provider": row[11],
                # 批 108·步 3（裁定 H）：`retention` ＝ 保留策略下界（date | None）。一列三义的
                # `start_floor` 已拆 ⇒ 本键是「窗口下界三地板」之一（设计 §5.1）；**引擎真读**
                # （`_window_floors`），不是装饰列。
                "retention": row[12]}


def _update_sync_state(sync_id: str, last_date: str | None, count: int, status: str = "idle"):
    """写终态（G-审 2026-08-18：status 支持 idle/partial/failed；调用方必须在本函数之后
    不再调 _mark_running(False)——后者会无条件覆盖回 'idle'）。

    last_date=None（配置游标本就为空）时写 NULL=不变，仅刷新 last_sync_ts——异常路径
    （P1-B 2026-10-03）用它表达「游标不动、只退避」。
    """
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
    except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
        pass
    return len(pd.date_range(start=start, end=end, freq="B"))


def _data_ready_end_date(lag_trade_days: int = 0) -> str:
    """增量窗口上界（YYYYMMDD）＝最近一个「数据已可用」的交易日。

    批 92 根因修复：原 `sync()` 一律以 `date.today()` 作窗口上界，而按 trade_date 的增量
    数据**当日盘中/次日 T+1 前均不可得** ⇒ 逐日循环的前沿日必然空拉；又因下游无条件把
    游标推到 end_date，**该前沿日永不复访 ⇒ 静默永久缺失**。确定性受害样本＝
    `margin_detail`（cron 09:00、T+1 数据，当日必空 ⇒ 全表恒空）。

    - lag_trade_days=0：今日若为交易日则取今日，否则取上一交易日。
    - lag_trade_days=1：**严格早于今日**的上一交易日（T+1 数据用，如 margin_detail）。
    - trade_cal 不可用/缺表 → 回退 `today - lag 自然日`（fail-open，绝不抛）。
    """
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT to_char(cal_date, 'YYYYMMDD') FROM trade_cal "
                "WHERE exchange='SSE' AND is_open=1 AND cal_date <= %s "
                "ORDER BY cal_date DESC LIMIT %s",
                (date.today(), lag_trade_days + 1))
            rows = cur.fetchall()
            if len(rows) > lag_trade_days:
                return rows[lag_trade_days][0]
    except Exception as e:   # noqa: BLE001 —— fail-open：日历缺失不得阻断同步
        logger.warning("交易日历读取失败（回退自然日上界）: %s", e)
    return (date.today() - timedelta(days=lag_trade_days)).strftime("%Y%m%d")


def _trade_dates_in_range(start_ts: str, end_ts: str) -> list[str] | None:
    """[start_ts, end_ts] 内的交易日（trade_cal is_open=1，SSE）。

    批 97：tier1 逐日循环的"不空跑"依据——freq="B" 只排周末，法定节假日全在空调用。
    返回 None ＝ 日历未覆盖该区间（fresh 库/深度不足/异常），调用方 fail-open 回
    freq="B"（宁多打、不漏拉）。覆盖度守卫：日历首行距区间起点 >3 天视为未覆盖
    （如库里只有 2026 一年而回补从 2010 起——只回补 2026 段会静默漏掉 2010-2025）。
    """
    try:
        from src.data_platform.db import get_conn as _gc
        d0 = f"{start_ts[:4]}-{start_ts[4:6]}-{start_ts[6:8]}"
        d1 = f"{end_ts[:4]}-{end_ts[4:6]}-{end_ts[6:8]}"
        with _gc() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cal_date, is_open FROM trade_cal "
                    "WHERE exchange='SSE' AND cal_date BETWEEN %s AND %s "
                    "ORDER BY cal_date", (d0, d1))
                rows = cur.fetchall()
        if not rows:
            return None
        if (rows[0][0] - date(int(start_ts[:4]), int(start_ts[4:6]), int(start_ts[6:8]))).days > 3:
            logger.warning("trade_cal 覆盖度不足（首行 %s 晚于区间起点 %s），回退 freq=B",
                           rows[0][0], d0)
            return None
        return [r[0].strftime("%Y%m%d") for r in rows if r[1]]
    except Exception as e:
        logger.warning("交易日历区间读取失败（回退 freq=B）: %s", e)
        return None


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
            handler = _sync_via_kind if sync_id in _VIA_KIND_IDS else _HANDLERS.get(sync_id)
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
            # 终态词：handler 可显式声明 log_status（批 83b——池内族收编后要保留原独立实现的
            # 'timeout'/'skipped' 两个词：时间盒中断既非失败也非健康心跳、撞锁轮次同理，
            # 15-服务监控的 tier2 心跳只认 done/success）。未声明=通用口径。
            status = r.get("log_status") or ("partial" if failed_dates else "success")
            duration_ms = int((time.time() - t0) * 1000)
            _log(sync_id, cfg["mode"], start_date, end_date, pulled, saved, duration_ms,
                 status, "", failed_dates, expected_days, actual_days)
            # 批 108·步 3（设计 §5.3「封顶不得静默」）：`policy_discard`（主动不保留）/
            # `unreachable`（源不可达）**不是缺口** ⇒ 只记日志、不进 failed_dates（不误告警）。
            # 批 109：由「只记日志」升级为**落表终态**（幂等，读-改-写；`sync_gap` 的终态出口）。
            _excl = r.get("excluded") or []
            if _excl:
                logger.warning("%s：排除段登记 %d 条 [%s]（主动不保留/源不可达，非缺口）",
                               sync_id, len(_excl),
                               ", ".join(sorted({f"{e.get('kind')}:{e.get('from')}..{e.get('to')}"
                                                 for e in _excl})))
                try:
                    _upsert_sync_gap([
                        (sync_id, e.get("symbol", ""), e["from"], e["to"], e["kind"],
                         e.get("reason")) for e in _excl
                        if e.get("kind") in _SYNC_GAP_EXCLUDED_STATES])
                except Exception as _e:
                    logger.warning("%s：排除段落表失败（不阻塞同步）: %s", sync_id, _e)
            # 批 109（输出闭环）：per-date 对账**本轮新 open** 的段 ⇒ 聚合告警（整轮 1 条、样本 5 段）。
            # ⚠️ 只响**新 open**（`new_segments`），**不是**「本轮仍缺全部段」（`segments`）——后者会让
            # 长期存量缺口每轮重响 ⇒ 告警疲劳、真缺口可见性被稀释；纪律与 per-symbol（`stat['new']`）
            # 同源（方案 v3 产出 3「告警聚合限响：每轮 1 条、只响新 open」）。
            _new = ((r.get("reconcile") or {}).get("new_segments")) or []
            if _new:
                _alert_sync_gaps(sync_id, [f"{a}~{b}" for a, b in _new])
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
                    # 批 92：无 last_success_date 键的 handler（tier1 按日族）改从返回体取
                    # 「游标上界」——它可能窄于 end_date（如 margin_detail 的 T+1 数据可用日
                    # ＝上一交易日）。缺省仍 end_date ⇒ 未声明者零行为变化。
                    # 批 104：终态词**如实反映失败**。原实现恒给 'idle'，而这一处同时
                    #   ①写进 `sync_config.last_status`、②被下方 `_alert_sync_failure` 取作
                    #   告警标题 ⇒ 有失败时**两处一起失真**（prod 实证：`last_status='idle'`
                    #   而 `sync_log.status='partial'`；告警标题成「数据同步 idle: okx_perp_daily」
                    #   正文却是「失败 7 项」）。故修**源头**而非只改告警词——一处真源。
                    #   ⚠ 游标推进语义**不变**（这两个族仍无条件推进：分钟线档若全市场失败
                    #   就冻游标会引发 beat 重试风暴，见上方 :384-387 与 G-S1）。只改词。
                    _term = ("idle" if not failed_dates
                             else ("partial" if saved else "failed"))
                    _advance = (r.get("cursor_upto") or end_date, saved, _term)
            else:
                _advance = None   # 回补不推进游标（现状）
            _mark_running(sync_id, False)   # G-S2：先清 running（置 idle），终态随后覆盖——
            #   若顺序颠倒，partial/failed 会被本行无条件覆盖回 idle（测试锁死此顺序）
            if _advance is not None:
                _update_sync_state(sync_id, *_advance)
            if failed_dates:
                # H 口径统一：告警用终态（failed/partial，与 sync_config.last_status 一致）。
                # 批 104：`_advance[2]` 不再对「无 last_success_date 族」恒给 'idle'
                #   （上方 :406 起如实给 idle/partial/failed）⇒ 告警标题与正文不再自相矛盾。
                _alert_sync_failure(sync_id, _advance[2] if _advance else status, failed_dates)
            return {"status": status, "rows_pulled": pulled, "rows_saved": saved,
                    "duration_ms": duration_ms, "failed_dates": failed_dates,
                    "expected_days": expected_days, "actual_days": actual_days,
                    "backfill": bool(backfill_from)}

        except Exception as e:
            # P1-B 修复（2026-10-03 数据同步验证）：**异常型失败同样推进 last_sync_ts**。
            # G-S3（上方 :285）只覆盖「handler 正常返回 + failed_dates」路径，漏了「handler
            # 抛异常」路径——原实现仅 _mark_running(False)，last_sync_ts 停旧值 ⇒ 调度器
            # base 恒旧（tasks.py:792）⇒ croniter.get_next 恒返回已过去的到点 ⇒ 每 300s
            # 重触发一次，永不停止（重试风暴）。
            # 实证：concept_sync 上游 Error 1054 后 last_sync_ts 停在 2026-09-25，本地
            # sync_log 积 1876 条、生产每 5 分钟一轮（24h 238 轮）。
            # 语义与「返回型全失败」对齐：**游标不动**（写回旧值，绝不写 NULL——写 NULL 会
            # 清掉游标让下轮全量重拉）、status=failed、last_sync_ts 刷新（退避的关键）。
            # 回补模式沿用「不碰游标/ts」现状（与上方 _advance=None 同源语义）。
            duration_ms = int((time.time() - t0) * 1000)
            _log(sync_id, cfg["mode"], "", end_date, 0, 0, duration_ms, "error", str(e)[:200])
            _mark_running(sync_id, False)   # G-S2：先清 running，终态随后覆盖
            if not backfill_from:
                _update_sync_state(sync_id, cfg["last_sync_date"], 0, "failed")
            # 异常型失败此前零告警（P1-A 被埋 1873 条无人知的直接原因）——补主动告警，
            # 与返回型失败同 wait 级（notify 同标题 1min 去重，不刷屏）。
            _alert_sync_failure(sync_id, "failed", [str(e)[:100]])
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
    # 批 99（闭合待办 98）：逐日循环接交易日历——freq="B" 只排周末，法定节假日全在空调用
    # （astock_basic 本函数是唯一调用方，回补 1990 起实测 603 次）。日历未覆盖区间则 fail-open
    # 回 freq="B"（宁多打、不漏拉，同批 97 口径）。交易日 ⊆ 工作日 ⇒ 收窄只会少拉节假日。
    _trade_days = _trade_dates_in_range(start, end)
    if _trade_days is not None:
        date_range = _trade_days
    else:
        date_range = [d.strftime("%Y%m%d")
                      for d in pd.date_range(start=start, end=end, freq="B")]
    total = len(date_range)
    total_pulled = 0
    total_saved = 0
    failed_dates: list[str] = []
    # F2 根因（G 审）：连续成功末日——第一个失败日之前的最后成功日；空 df 记成功（G-S4：
    # 节假日 freq="B" 会拉到空数据，若记失败游标永久卡死在节前）
    last_success_date: str | None = None
    broken = False

    for i, d in enumerate(date_range, 1):
        # 批 99：date_range 可能是交易日字符串列表（接日历路径）或 Timestamp（fail-open 路径）
        trade_date = d if isinstance(d, str) else d.strftime("%Y%m%d")
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

# ─── 批 107：7 个字面量供给项（bespoke handler——保留各自写入/SM 侧效应） ───
# 与 `_TIER1_BATCH`/`_TIER1_FULL`（走通用工厂）并列：本表只声明**归置键**（真源
# `sync_kind_config`），供 ① 对账门逐项校验（漂移即红）② `fetch_supply` 调用点集中。
# 拉取经 `_get_supply_adapter(cfg).fetch_supply(kind, sub_kind, …)` ⇒ `sync_config.provider` 生效。
# 列形状真源＝`sync_kind_config`（`base.py::fetch_supply` 立法）；本表不声明列。
_LITERAL_SUPPLY: dict[str, tuple[str, str | None, str]] = {
    "astock_basic":      ("fundamental_daily", None,          "daily_basic"),
    "astock_list":       ("static_list",       "stock",       "asset_static_info"),
    "static_symbols":    ("static_list",       "symbols",     "static_symbols"),
    "cb_basic":          ("static_list",       "convertible", "cb_basic_info"),
    "convertible_terms": ("static_list",       "terms",       "convertible_terms"),
    "etf_list":          ("static_list",       "etf",         "etf_basic_info"),
    "trade_cal":         ("trade_cal",         None,          "trade_cal"),
}


def _sync_astock_basic(cfg: dict, end_date: str, backfill_from: str | None = None,
                       progress_cb: Callable | None = None) -> dict:
    """A股基本面指标同步（按日期批量拉取，一次全市场）。"""
    from src.data_platform.adapters.tushare_adapter import save_daily_basic   # save 面（批 107 不动）
    kind, sub, _tbl = _LITERAL_SUPPLY["astock_basic"]
    adapter = _get_supply_adapter(cfg)      # 批 107：拉取经 adapter（provider 生效）
    # 批 108·步 3：下界＝三方地板（per-date 族 ⇒ 家族级 `max(retention, source_earliest)`）。
    # 本项是 `retention='1990-12-19'`（迁移 0139 逐行归义）**唯一的引擎消费点**——不接则该 seed
    # 是死值（死构件）。行为零漂移：无游标时 `max(today−7d, 1990-12-19)`＝today−7d，有游标时
    # `max(cursor+1, ·)`＝cursor+1。
    _floor, _excl = _family_start(adapter, kind, cfg)
    _floor_s = _floor.strftime("%Y%m%d")
    if backfill_from:
        start = max(backfill_from, _floor_s)
    else:
        last = cfg.get("last_sync_date") or (date.today() - timedelta(days=7)).strftime("%Y%m%d")
        start = max((pd.Timestamp(last) + timedelta(days=1)).strftime("%Y%m%d"), _floor_s)
        if start > end_date:
            return {"pulled": 0, "saved": 0, "start": start, "failed_dates": [], "expected_days": 0, "actual_days": 0}

    r = _sync_by_trade_date(lambda trade_date: adapter.fetch_supply(kind, sub, trade_date=trade_date),
                            lambda df: save_daily_basic(df), start, end_date,
                            api_name=_api_name_of(cfg), progress_cb=progress_cb,
                            provider=_provider_of(cfg))
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


# 批 108·步 2：`security_master.session_id` 的显式取值（真源＝`market_hours.session_id` 主键，
# 迁移 0092 种子 `astock_main`/`crypto_247`）。该列 `NOT NULL DEFAULT 'astock_main'`——
# **必须显式传**：否则 crypto 行会静默落进 A 股 session（`SMClient.upsert_rows` 已加
# 「13 列且 session_id 非空」校验兜底，漏传即响亮 ValueError）。
SM_SESSION_ASTOCK = "astock_main"
SM_SESSION_CRYPTO = "crypto_247"


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
    kind, sub, _tbl = _LITERAL_SUPPLY["astock_list"]
    prov = _provider_of(cfg)      # 批 83b：provider 真路由（原死钉 tushare）
    adapter = _get_supply_adapter(cfg)   # 批 107：拉取经 adapter（原 `_get_pro` 直连 `stock_basic` 已移除）
    # 批 67：裸调收编（不传 min_interval=档值两级取保 DB 覆写）
    from src.data_platform.rate_limit import rate_limit_context
    _ds = _get_rate_ds(prov)      # 批 83b：原两处重复解析同一 provider，收成一次
    with rate_limit_context(_ds, "stock_basic"):
        df = adapter.fetch_supply(kind, sub)   # DB 优化：网络拉取在事务外（2026-08-21 盘点）
        _ds.record_usage(api_calls=1, api_name="stock_basic", provider=_ds.provider)
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
                100, 0.01, "T+1", r[5], r[6], SM_SESSION_ASTOCK)
               for r in rows if r[0])
    return {"pulled": len(df), "saved": len(df), "start": end_date,
            "failed_dates": [], "expected_days": None, "actual_days": None}


def _sync_cb_basic(cfg: dict, end_date: str, backfill_from: str | None = None,
                   progress_cb: Callable | None = None) -> dict:
    """可转债基本信息全量同步。"""
    kind, sub, _tbl = _LITERAL_SUPPLY["cb_basic"]
    adapter = _get_supply_adapter(cfg)   # 批 107：拉取经 adapter
    from src.data_platform.rate_limit import rate_limit_context
    with rate_limit_context(_get_rate_ds(_provider_of(cfg)), "cb_basic"):   # 批 64b 裸调收编（档 0.3s）
        df = adapter.fetch_supply(kind, sub)   # DB 优化：拉取在事务外
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
    # 批 108 步 4 盲审必修-1：cb 行元组是 **15 元**（`rate_clause` 在 `r[12]`）⇒
    # `list_date/delist_date` 是 `r[13]/r[14]`，原写 `r[12]/r[13]` 把 `rate_clause` 当
    # `list_date`（`_null_date` 过滤非日期串 → NULL）、把 `list_date` 当 `delist_date`
    # （**上市即退市**）。见 `盲审批108代码-同判综合.md` 必修-1。
    _sm_upsert(((vt := _ts_to_vt_prefix(r[0])), "astock",
                (vt.rsplit(".", 1) + [""])[1], "convertible", None, r[1], None,
                10, 0.001, "T+0", r[13], r[14], SM_SESSION_ASTOCK)
               for r in rows if r[0])
    def _norm_date(s, fallback="") -> str:
        """YYYYMMDD→YYYY-MM-DD；脏值（空串/'None'/NaN）回落 fallback（发行日 list_date）再兜 2010-01-01。"""
        def _ok(v):
            return v if isinstance(v, str) and v.isdigit() and len(v) == 8 else None
        s, fb = _ok((s or "").strip() if isinstance(s, str) else ""), _ok(
            (fallback or "").strip() if isinstance(fallback, str) else "")
        v = s or fb
        return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if v else "2010-01-01"
    # NaN 过滤（盲审 B：NaN 进 json.dumps 产非法 JSON 毒化整批）
    # 批 108 步 4 盲审必修-1（同族）：fallback 按 docstring 是「发行日 list_date」＝`r[13]`，
    # 原写 `r[12]`＝`rate_clause` ⇒ 文档承诺的回落**永不生效**、静默退 `2010-01-01`。
    _sm_upsert_state(((vt := _ts_to_vt_prefix(r[0])), _norm_date(r[8], r[13]),
                       "conv_price", {"conv_price": float(r[7])})
                      for r in rows if r[0] and r[7] is not None and not pd.isna(r[7]))
    return {"pulled": len(df), "saved": len(df), "start": end_date,
            "failed_dates": [], "expected_days": None, "actual_days": None}


def _sync_etf_list(cfg: dict, end_date: str, backfill_from: str | None = None,
                   progress_cb: Callable | None = None) -> dict:
    """ETF基金列表全量同步。"""
    kind, sub, _tbl = _LITERAL_SUPPLY["etf_list"]
    adapter = _get_supply_adapter(cfg)   # 批 107：拉取经 adapter
    from src.data_platform.rate_limit import rate_limit_context
    with rate_limit_context(_get_rate_ds(_provider_of(cfg)), "fund_basic"):   # 批 64b 裸调收编（档 0.3s）
        df = adapter.fetch_supply(kind, sub)   # DB 优化：拉取在事务外
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
                100, 0.001, "T+1", r[5], None, SM_SESSION_ASTOCK)
               for r in rows if r[0])
    return {"pulled": len(df), "saved": len(df), "start": end_date,
            "failed_dates": [], "expected_days": None, "actual_days": None}


def _sync_trade_cal(cfg: dict, end_date: str, backfill_from: str | None = None,
                    progress_cb: Callable | None = None) -> dict:
    """交易日历同步。批 97：backfill_from 起**逐年**拉到当年（每年 1 次调用）——
    原实现无视 backfill_from 只拉当年，导致 trade_cal 恒 365 行、tier1 交易日
    循环与 _data_ready_end_date 在深回补区间无日历可用（fail-open 空跑）。

    批 107：拉取改经 `adapter.fetch_supply("trade_cal", None, year=y)`（**纯读**），
    写入（`init_trade_calendar` + INSERT）从旧 `pull_trade_cal` 搬入本 handler。
    """
    from src.data_platform.db import init_trade_calendar
    from src.data_platform.rate_limit import rate_limit_context  # 批 67：裸调收编（顺手）
    kind, sub, _tbl = _LITERAL_SUPPLY["trade_cal"]
    year_now = date.today().year
    year0 = int(backfill_from[:4]) if backfill_from else year_now
    prov = _provider_of(cfg)      # 批 83b：provider 真路由（原死钉 tushare）
    adapter = _get_supply_adapter(cfg)   # 批 107
    _ds = _get_rate_ds(prov)
    pulled = 0
    for y in range(year0, year_now + 1):
        with rate_limit_context(_ds, "trade_cal"):
            df = adapter.fetch_supply(kind, sub, year=y)
            _ds.record_usage(api_calls=1, api_name="trade_cal", provider=_ds.provider)
        if df is None or df.empty:
            continue
        rows = [(r["exchange"], r["cal_date"], int(r["is_open"]), r.get("pretrade_date"))
                for r in df.to_dict("records")]
        init_trade_calendar(y)      # 表已在 0001 建，保留接口兼容（当前为 no-op）
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO trade_cal (exchange, cal_date, is_open, pretrade_date) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT (exchange, cal_date) DO NOTHING",
                    rows,
                )
            conn.commit()
        pulled += len(rows)
    return {"pulled": pulled, "saved": pulled, "start": end_date,
            "failed_dates": [], "expected_days": None, "actual_days": None}


# ─── 批 83b：原独立 beat 收编为 sync_config 驱动的 handler ───
# 立法（任务书 83b 点 3）：tier1 九键自 0045 起已是"配置驱动"，真硬编码的只剩这四条 beat；
# 收编=tasks.py 的独立 @app.task 逻辑搬进 engine，beat 条目从 app.py 退役，改由
# sync_config 行 + data_sync_scheduler（300s 扫描 cron）统一调度——与 tier1 同等待遇。
# 收编后一律经 sync()：拿到防重 SyncLock、sync_log 留痕、失败告警、游标三态，不再各写一套。

def _sync_static_list(cfg: dict, end_date: str, backfill_from: str | None = None,
                      progress_cb: Callable | None = None) -> dict:
    """静态标的清单同步（F-DATA-004）——原 `tasks.static_list_sync` 收编（批 83b）。

    拉 `pro.stock_basic`（在市）→ upsert `static_symbols`（退市标记由 delisted 列保留，
    本路径只写在市行）。原实现语义照搬：网络拉取在事务外 + executemany 一次提交
    （2026-08-21 盘点重灾 #1：原为"开事务→事务内网络拉取 5400 行→逐行 upsert"）。
    """
    kind, sub, _tbl = _LITERAL_SUPPLY["static_symbols"]
    prov = _provider_of(cfg)
    from src.data_platform.rate_limit import rate_limit_context
    _ds = _get_rate_ds(prov)
    pulled = 0
    try:
        adapter = _get_supply_adapter(cfg)   # 批 107：拉取经 adapter（原 `_get_pro` 直连已移除）
        with rate_limit_context(_ds, "stock_basic"):
            df = adapter.fetch_supply(kind, sub, fields="ts_code,name,industry")
            _ds.record_usage(api_calls=1, api_name="stock_basic", provider=_ds.provider)
    except Exception as e:
        return {"pulled": 0, "saved": 0, "start": end_date,
                "failed_dates": [f"pull:{type(e).__name__}:{str(e)[:60]}"],
                "expected_days": 1, "actual_days": 0}
    if df is None or df.empty:
        return {"pulled": 0, "saved": 0, "start": end_date,
                "failed_dates": [], "expected_days": 0, "actual_days": 0}
    pulled = len(df)
    rows = [(r["ts_code"], r.get("name", "") or "", r.get("industry", "") or "")
            for r in df.to_dict("records")]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO static_symbols (ts_code,name,industry,list_status,delisted) "
                "VALUES (%s,%s,%s,'L',false) "
                "ON CONFLICT (ts_code) DO UPDATE SET name=EXCLUDED.name,"
                "industry=EXCLUDED.industry,list_status='L',delisted=false,updated_at=now()",
                rows)
        conn.commit()
    return {"pulled": pulled, "saved": len(rows), "start": end_date,
            "failed_dates": [], "expected_days": 1, "actual_days": 1}


def _sync_convertible_terms(cfg: dict, end_date: str, backfill_from: str | None = None,
                            progress_cb: Callable | None = None) -> dict:
    """可转债条款数据同步（D3）——原 `tasks.convertible_terms_sync` 收编（批 83b）。

    拉活跃可转债清单 → 逐只 `pull_cb_basic` → upsert `convertible_terms`。
    **保留原语义**：只取前 50 只（原代码 `bonds[:50]`——条款接口慢，单轮限量，
    靠每日重复覆盖）；逐只失败只记 failed 不中断整轮。
    """
    from src.data_platform.rate_limit import rate_limit_context
    kind, sub, _tbl = _LITERAL_SUPPLY["convertible_terms"]
    prov = _provider_of(cfg)
    adapter = _get_supply_adapter(cfg)   # 批 107：拉取经 adapter（原直连 pull_convertible_bonds/pull_cb_basic）
    _ds = _get_rate_ds(prov)
    try:
        with rate_limit_context(_ds, "cb_basic"):
            df_bonds = adapter.fetch_supply(kind, sub)      # 全量清单（纯读 df，取 ts_code）
            _ds.record_usage(api_calls=1, api_name="cb_basic", provider=_ds.provider)
        bonds = df_bonds["ts_code"].tolist() if df_bonds is not None and not df_bonds.empty else []
    except Exception as e:
        return {"pulled": 0, "saved": 0, "start": end_date,
                "failed_dates": [f"pull_list:{type(e).__name__}:{str(e)[:60]}"],
                "expected_days": 1, "actual_days": 0}
    failed: list[str] = []
    saved = 0
    for ts_code in (bonds or [])[:50]:
        try:
            with rate_limit_context(_ds, "cb_basic"):
                df1 = adapter.fetch_supply(kind, sub, ts_code=ts_code)   # 单只条款（纯读 df）
                _ds.record_usage(api_calls=1, api_name="cb_basic", provider=_ds.provider)
            terms = df1.iloc[0].to_dict() if df1 is not None and not df1.empty else {}
            if terms:
                with get_conn() as conn:
                    conn.execute("SELECT 1 FROM convertible_terms LIMIT 1")
                    conn.execute(
                        "INSERT INTO convertible_terms (ts_code, terms, updated_at) "
                        "VALUES (%s,%s,now()) "
                        "ON CONFLICT (ts_code) DO UPDATE SET terms=EXCLUDED.terms, updated_at=now()",
                        (ts_code, jsonb(terms)))
                    conn.commit()
                saved += 1
        except Exception as e:
            failed.append(f"{ts_code}:{type(e).__name__}")
            logger.warning("_sync_convertible_terms 处理 %s 失败: %s", ts_code, e)
            continue
    return {"pulled": len(bonds or []), "saved": saved, "start": end_date,
            "failed_dates": failed, "expected_days": 1,
            "actual_days": 1 if not failed else 0}


def _make_pool_handler(full: bool):
    """池内深度数据 handler 工厂（批 83b 收编 last 两条 beat）。

    full=False=5 分钟增量轮（`pool_data`）；full=True=周日全量校准
    （`pool_data_full_calibrate`，无视游标窗口，游标照常推进）。两条同源不同参，
    故用工厂同 `_make_tier1_handler`/`_make_full_rebuild_handler` 先例，不写 if 分派。

    返回值适配 sync() 的 handler 契约：标的数→rows_pulled（原口径：sync_log.rows_pulled
    存标的数，`routes/sync.py` progress 端点按此展示）；逐标的错误→failed_dates（触发
    partial 终态 + 失败告警）；`log_status` 透传原实现的状态词（timeout/skipped——
    见 sync() 内的口径说明）。
    """
    def _handler(cfg: dict, end_date: str, backfill_from: str | None = None,
                 progress_cb: Callable | None = None) -> dict:
        from .pool_data import _ROUND_LOG_STATUS, run_pool_sync
        r = run_pool_sync(cfg, full=full)
        return {"pulled": r.get("symbols", 0), "saved": r.get("saved", 0),
                "start": end_date, "failed_dates": list(r.get("errors") or []),
                "expected_days": None, "actual_days": None,
                "log_status": _ROUND_LOG_STATUS.get(r.get("status"))}
    return _handler


_MINUTE_FREQ = {"astock_minute": "1min", "astock_minute_5min": "5min"}


def _save_bars(rows: list[tuple]) -> int:
    """写入 bar_1D 表。批 71：旧 validate_bar_quality 坏调用（传 rows 恒 AttributeError 被吞，
    自引入 0 次成功）已删——质量校验上移至持 Tushare 原生 df 的调用侧（_log_bar_quality）。"""
    if not rows:
        return 0
    from src.data_platform.db import save_bars
    return save_bars("1D", rows)


def _log_bar_quality(df, label: str) -> None:
    """批 71：日线质量校验（fail-soft——校验器任何异常只 warning 不阻入库，对齐旧 :692-698
    语义；v2 #1 双同 P1）。输入=Tushare 原生 df（ts_code/trade_date YYYYMMDD/ohlc/vol/pre_close）；
    issues 一行一条（观察期统计友好——v2 #16）。"""
    try:
        from src.data_platform.adapters.tushare_adapter import validate_bar_quality
        q = validate_bar_quality(df)
        for issue in q.get("issues") or []:
            logger.warning("[quality] %s %s", label, issue)
    except Exception as e:
        logger.warning("[quality] %s 校验器异常（不阻入库）: %s", label, e)


# 批 72 v2 #6：质量校验 label 词表映射（沿用批 71——观察期查询零改动）。
# per-symbol 三调用方只传 kind 词形（astock/etf/cb，源自 _PER_SYMBOL_META）——sub_kind
# 词形（stock/convertible）到不了本 map（base.py fetch 面 label 内联不经此 map）。
_QUALITY_LABEL_OF = {"astock": "bar_daily", "etf": "etf_daily", "cb": "cb_daily"}


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
    from datetime import date as _date
    from datetime import timedelta as _td

    from src.data_platform.data_source import TushareDataSource, get_data_source
    from src.data_platform.rate_limit import rate_limit_context
    from src.data_platform.schema import to_vt_symbol
    # 批 83b：显式 tushare 例外（P2）。adj_factor 是 Tushare 独有接口（pull_adj_factor），
    # 本函数也无 cfg 可读 provider（手动/定时回补入口，非 sync_config 驱动）。此处
    # `get_data_source("tushare") or TushareDataSource()` 是 tushare 自身"DB 无行→.env"
    # 的合法回退，**不是**串源（provider 就是 tushare）。与 index_daily 的例外同理。
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


# ═══ 批 101：加密（币安 USDT-M 永续）日线 · 批 102b：OKX 永续日线 ═══
# 两所共用同一条 crypto bar 链（本工厂 + `_crypto_end`/`_crypto_base_start`），差异只在 adapter（源侧）与
# 配置行（sync_id / 表 / 调度）。与 A 股族的**三处结构性差异**（写在代码边上，避免下一个人
# 按 A 股心智改它）：
# 1) **形态**＝按标的 × 时间窗（crypto 无「全市场单日」概念）⇒ 不走 `_sync_via_kind` 逐日批路径；
# 2) **日历**＝连续轴（无 trade_cal）⇒ 不用 `_trade_dates_in_range` / `freq="B"`；
# 3) **限速**＝批量站/公共 API 无 weight 模型（币安＝静态文件 CDN；OKX＝IP 级窗口，由 adapter
#    自持）⇒ **刻意不套 `rate_limit_context`**：`_get_rate_ds(<provider>)` 无 DB 行会回落
#    tushare 兜底源并告警「串源风险」（把加密下载计进 tushare 的熔断器是错的）。

# ═══════════ 批 108·步 3：窗口下界三地板（设计 §5.1 / 裁定 B·H）═══════════

# ⚠️ 裁定 B（「任务列为准 + kind 默认作默认值来源」）在本批的**落地形态**：kind 默认由
# **迁移 0139 的 seed（任务列）** 承担（`index_daily` / `astock_basic` 的绝对起点），
# 代码侧**不设** kind 默认表。理由（架构）：kind 级默认若取**相对**值（如「前推 7 天」），
# 它就是设计 §5.1 点名要消灭的**假首跑地板**——「用一个假值同时冒充 inception 与 retention」
# 换了个名字回来，且会**静默**把窗口压成滚动区间（甚至压空 ⇒ `start > end` 无声 no-op）；
# 而 minute 族的 kind 级**绝对**起点**不存在自然值**（要「从哪一分钟起负责」是**策略**，
# 只能逐任务声明）。⇒ 未声明 `retention` 就是**「从源头全要」**这个可读的语义。
# 连带（**启用前必读**）：`astock_minute` / `astock_minute_5min` 现为 `enabled=false` 且游标
# 为 NULL ⇒ 开用前**必须**先显式填 `retention`（否则首跑＝2010 起的全部分钟）。

# 三地板全空时的**有界**兜底（配响亮告警——不静默退化成全史）。
_WINDOW_FALLBACK_DAYS = 30


def _ymd(s) -> date:
    """`'YYYY-MM-DD'` / `'YYYYMMDD'` / `date` → `date`。"""
    if isinstance(s, date):
        return s
    t = str(s).strip().replace("-", "").replace("/", "")
    return date(int(t[:4]), int(t[4:6]), int(t[6:8]))


# 哨兵：区分「未传 inception」与「显式传 None（未知）」（`_window_floors` 的批量预取口）。
_UNSET = object()


def _window_floors(adapter, kind: str, cfg: dict, *, sym: str | None = None,
                   inception: object = _UNSET,
                   ) -> tuple[date | None, list[dict]]:
    """**三方地板取 max**：`max(inception, retention, source_earliest)`（设计 §5.1）。

    返回 `(地板日 | None, 排除段登记)`；`None` ⇒ 三源皆未声明（调用方须给有界缺省 + 告警）。

    - **inception**（生命周期主档）：仅 per-symbol 族有，取 **`sm_inception(sym)`**（SM 单一真源，
      裁定 G①／批 110 收编——原读 `adapter.symbol_inception` 活体，该活体只有 OKX 有实现，
      使 binance/tushare/joinquant 恒 `None`、窗口下界静默退化）；`None` ＝ **显式「未知」**
      （裁定 F，禁用源可达性冒充）。`sym=None` ⇒ 家族级（per-date 族本就没有逐个标的的
      生命周期 ⇒ 该族只能靠 `retention` ＋ 源界，设计 §九.7）。
      ⚠️ **`sym` 的形态＝`vt_symbol`**（含交易所后缀，如 `BTCUSDT.BINANCE`）——SM 按该键精确查
      （模糊匹配＝第二真源）。crypto 拉取路径的 `list_symbols()` 返回**源侧形态**，调用方须先
      `_vt_of_source()` 转换。
    - **retention**（策略）：`sync_config.retention`（任务级，裁定 B）；未声明 ⇒ kind 默认。
    - **source_earliest**（源属性）：`adapter.available_range(kind)[0]`（批 108·步 1 硬契约）——
      **未实现即响亮抛** `NotImplementedError`（禁以 today/(None,None) 冒充）。

    `inception` kwarg 显式传入时**不再查 SM**（`_UNSET` ⇒ 内部查 `sm_inception(sym)`）——
    `_reconcile_symbols` 用 `_sm_universe` **批量预取**的 `list_date` 传入，**消除 per-symbol 的
    N+1**（读取规模契约：成本由 scope 数决定，不由标的数决定）。

    排除段登记（设计 §5.3「**封顶不得静默**」，只在该段非空时登记）：
    - `unreachable` ＝ `[inception, source_earliest)`：**存在**但批量源不可达（源界绑定）；
    - `policy_discard` ＝ `[max(inception, source_earliest), retention)`：我们**主动不保留**（带理由）。
      ⚠️ 判据用**已知下界**（`max(inception, source_earliest)`）而非仅 `inception`——否则
      inception 未知的源（裁定 F）在 `retention` 截窗时**永不登记**（步骤 4 盲审必修-2）。
    ⚠️ 本批只**登记 + 日志**（进 handler 返回体 `excluded`）；落表 `sync_gap` ＝ 批 109。
    """
    if inception is _UNSET:
        inception = sm_inception(sym) if sym is not None else None
    src_lo, _src_hi = adapter.available_range(kind)      # fail-loud 硬契约（批 108·步 1）
    ret = cfg.get("retention")
    ret_s = ret.strftime("%Y-%m-%d") if ret else None

    known = {k: _ymd(v) for k, v in (("inception", inception), ("retention", ret_s),
                                     ("source_earliest", src_lo)) if v}
    floor = max(known.values()) if known else None

    excl: list[dict] = []
    # 批 109：登记项带 `symbol`（`''`＝家族级，per-symbol 调用给出标的）——落表
    # `sync_gap` 时它是 scope 键的一部分（UNIQUE＝sync_id+symbol+gap_start）。
    _sym = sym or ""
    if inception and src_lo and _ymd(src_lo) > _ymd(inception):
        excl.append({"kind": "unreachable", "symbol": _sym,
                     "from": str(inception), "to": str(src_lo),
                     "reason": f"{getattr(adapter, 'provider', '?')} 源可达下界（实测）"})
    # 批 108 步 4 盲审必修-2：`policy_discard` 判据＝「已知下界」而非仅 `inception`。
    # `retention` 抬升下界时，`[已知下界, retention)` 段是**我们主动不保留**（§5.3「封顶不得静默」）。
    # 只用 `inception` 会让**inception 未知的源**（裁定 F：Binance 落 NULL）在 `retention`
    # 截窗时**永不登记**（静默封顶）——实例 `binance_perp_hourly`（retention 2026-09-29
    # vs source 2019-12-31、inception NULL ⇒ 静默弃约 7 年）。
    _lower = [_ymd(v) for v in (inception, src_lo) if v]
    lo_known = max(_lower) if _lower else None
    if ret_s and lo_known and _ymd(ret_s) > lo_known:
        excl.append({"kind": "policy_discard", "symbol": _sym,
                     "from": lo_known.isoformat(), "to": ret_s,
                     "reason": "任务 retention 晚于已知下界（上币日/源界）⇒ 该段为主动不保留（非不存在）"})
    return floor, excl


def _family_start(adapter, kind: str, cfg: dict, fallback_days: int = _WINDOW_FALLBACK_DAYS,
                  ) -> tuple[date, list[dict]]:
    """**家族级**下界 ＝ `max(retention, source_earliest)`（per-date 族；不含 per-symbol inception）。

    三源皆未声明 ⇒ 回退 `fallback_days` 天**并响亮告警**（有界兜底，不静默全史）。
    """
    floor, excl = _window_floors(adapter, kind, cfg, sym=None)
    if floor is None:
        floor = date.today() - timedelta(days=fallback_days)
        logger.warning("窗口下界三源皆未声明（kind=%s sync=%s）⇒ 有界回退 %d 天；"
                       "请补 sync_config.retention 或 adapter.available_range",
                       kind, cfg.get("id"), fallback_days)
    return floor, excl


def _crypto_end(end_date: str, adapter, kind: str) -> date:
    """crypto 窗口上界（含）＝ `min(end_date, today(UTC) − publish_lag(kind))`（设计 §5.1 第四边界）。

    `data.binance.vision` 实测：UTC 当日文件 404、前一日 200 ⇒ 上界须扣发布滞后，否则每轮都把
    「尚未落盘的今天」记成失败日，游标永不动。滞后本身是**源属性**（`adapter.publish_lag`），
    原先只是常量散文里的隐含值——批 108·步 3 搬到接入层（设计 §六「并入 publish_lag」）。

    ⚠️ **上界「扣滞后」≠「已发布」**（批 101b 复核）：实测 UTC 02:46 时 D-1 在全部 interval 上
    仍 404 ⇒ 窗口末日通常仍在**批量站未发布区**。这不是本函数的错（上界必须 ≥ 昨日才不漏），
    而是**游标侧契约**：见 handler「游标＝实际取到数据的最后一日」那段。

    **日界（批 102b）**：「UTC 锚定」是两所**必须一致**的口径——币安日线本就是 UTC 日界；
    OKX 的 `bar=1D` 是 **UTC+8** 日界，故 `OkxAdapter` 一律 `bar=1Dutc` 对齐。
    **两源日界不同裸混一张 `bar_1d` 是静默错位**，一致性是前提而非风格。
    """
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    return min(_ymd(end_date),
               _dt.now(_tz.utc).date() - _td(days=int(adapter.publish_lag(kind))))


def _crypto_base_start(cfg: dict, backfill_from: str | None, adapter, kind: str,
                       ) -> tuple[date, list[dict]]:
    """crypto **家族级**窗口下界：游标在 ⇒ 游标＋1；无游标 ⇒ 三方地板（设计 §5.1）。

    ⚠️ **本批（步 3）之前**无游标时给 `end − first_run_days + 1`（首跑 30/7/1 天）——那是
    **假首跑地板**（「用一个假值同时冒充 inception 与 retention」，设计 §5.1），已换掉：
    crypto 族游标**从未闭合**（`last_sync_date` 全 NULL）⇒ 首跑下界＝`max(retention, 源界)`，
    即 **F-1「首跑即全史」**（全史由 `retention` 声明裁短，不由假天数裁）。
    """
    fam, excl = _family_start(adapter, kind, cfg)
    if backfill_from:
        return max(_ymd(backfill_from), fam), excl      # 回补显式起点，仍受地板夹（源拿不到更早）
    last = cfg.get("last_sync_date")
    if last:
        return _ymd(last) + timedelta(days=1), excl
    return fam, excl


# sync_id → (kind, sub_kind, freq)
#   kind/sub_kind = `fetch_supply` 分派键（与 sync_kind_config 归置行**必须一致**，由
#     tests/test_batch101b_* 的真库对账钉守——代码是声明面，DB 行不是第二真源）
#   freq = 内部 K 线频率；**同时是 `bar_{freq.lower()}` 表名后缀**（db.save_bars 的
#     insert 模板即如此），故 pg_table 不需要在代码里再写一遍
#   ⚠️ 批 108·步 3：原第 4 元 `first_run_days`（假首跑地板）已删——首跑下界改由
#     `max(inception, retention, source_earliest)` 决定（见 `_crypto_base_start`）。
_BINANCE_BAR_SPECS: dict[str, tuple[str, str, str]] = {
    "binance_perp_daily": ("bar_daily", "perp", "1D"),
    "binance_perp_hourly": ("bar_minute", "perp", "1h"),
    "binance_perp_1min": ("bar_minute", "perp", "1min"),
    "binance_perp_15min": ("bar_minute", "perp", "15min"),
}

# 「整个窗口都落在批量站未发布尾区」的容忍天数（见 handler 内 0 行判定）。
# ⚠️ 与 `publish_lag` 的**分工**：`publish_lag` 定**窗口上界**（源属性）；本常量只定
# 「0 行算**正常等待**还是**故障**」的**归类阈值**。上界搬走后本常量只剩归类职责，故保留。
_CRYPTO_TAIL_GRACE = 2

# 批 105：partial 冻结时游标回退的**下限**（相对窗口 end 的天数）——防 ratchet：
# 若某标的**永久**失败（下架/停牌态漏过滤），冻结语义会让重拉窗逐日增大；
# 卡在 `end-10d` 让单轮重拉有界（≈11 天 × 全标的），更老的洞靠人工 backfill（告警已响）。
_PARTIAL_FREEZE_MAX_DAYS = 10


def _sm_upsert_crypto(adapter, syms) -> None:
    """批 108·步 2：crypto 永续的**身份 + 生命周期**写入 `security_master`（裁定 A / G①）。

    行形状（13 列，见 `SMClient.upsert_rows`）：

    - `market='crypto'`、`exchange=adapter.venue`（`BINANCE`/`OKX`）、`category='perp'`
    - `session_id='crypto_247'`、`trade_phase='T+0'`（crypto 24/7 连续、当日可平）
    - `list_date = adapter.symbol_inception(sym)`——**真上币日**；不可得 ⇒ `None`
      （**显式「未知」**，裁定 F：禁用源可达性/首行数据冒充；对账按 `uncertain` 处理）
    - `vt_symbol = <源符号>.<venue>`（与 `to_bar_rows` 的落库形态同源，防 SM 与 bar 表两套键）

    **为什么在拉取前写**：元数据与拉取成败无关，且**对账需要期望集**——即使本轮全窗 0 行
    （OKX 7/485 那种「某标的整窗无数据」），SM 也须已有该标的，否则期望集为空 ⇒ **对账看不见它**。

    ⚠️ **元数据可得性（本批边界，挂账 G-4）**：`multiplier`（合约面值）/ `tick_size`（最小变动）
    在**批量站拿不到**（Binance `fapi` 被墙 ⇒ 无 `exchangeInfo`；OKX `instruments` 有
    `ctVal`/`tickSz` 但未接线）。⇒ 当前落**列默认值**（1 / 0.01），对 crypto **目前零消费者**
    （消费方是回测费用/限价对齐，均未接 crypto）故无实害；但**语义上是近似**，
    接 crypto 回测/交易前必须补真值。
    """
    venue = adapter.venue
    if not venue:
        # 单交易所源的 adapter 必须声明 venue——否则 vt_symbol 拼不出、SM 行全脏
        raise RuntimeError(f"{adapter.provider} adapter 未声明 venue（crypto SM 写入必需）")
    rows = []
    for sym in syms:
        s = str(sym)
        vt = s if s.upper().endswith("." + venue) else f"{s}.{venue}"
        rows.append((vt, "crypto", venue, "perp", None, None, None,
                     1, 0.01, "T+0", adapter.symbol_inception(sym), None, SM_SESSION_CRYPTO))
    _sm_upsert(rows)


def _make_crypto_bar_handler(sync_id: str, kind: str, sub_kind: str, freq: str,
                             *, label: str, enum_hint: str,
                             freeze_on_partial: bool = False):
    """工厂：crypto 永续 bar 族 handler（批 101 币安；批 102b 泛化到 provider 无关）。

    `label`＝日志/错误里的源名（`binance`/`okx`）；`enum_hint`＝符号枚举失败的可能原因
    （币安＝S3 list；OKX＝instruments 接口）。**只此两处 provider 差异**——其余逻辑
    （窗口、游标、失败可见性、回补 overwrite）两所完全同构，故一条实现，不复制两份。

    **窗口（批 108·步 3）**：上界＝`today(UTC) − publish_lag`；下界＝家族级
    `max(retention, source_earliest)`（`_crypto_base_start`），再 **per-symbol** 叠
    `max(., inception(sym))`（设计 §5.1「标的×日」；inception 未知 ⇒ 家族起点）。原「首跑给 N 天」
    的假地板已删——`retention` 才是「我们关心的最早时点」的表达口。

    **游标契约（批 101b 修正，勿回退）**：`cursor_upto` ＝ **实际取到数据的最后一日**，
    而不是窗口上界。原实现取窗口上界（`end_s`），与批量站的发布滞后叠加后**必然吃日成洞**：

      08:30 北京 = 00:30 UTC 跑 ⇒ `end = min(end_date, UTC 昨日)` = D-1；但实测 D-1 的文件
      此刻**还没发布**（2026-10-06 02:46 UTC 复核：10-05 在全部 interval 上仍 404）。
      于是该轮 D-1 取到 0 行，而游标仍被推到 D-1 ⇒ 下一轮 `start = D-1+1 = D`
      ⇒ **D-1 永不重试**（静默数据洞，且 sync_log 记 success）。

    取「实际取到数据的最后一日」后：D-1 未发布 ⇒ 游标停在 D-2 ⇒ 下一轮 `start = D-1`
    自动重试；一旦发布即补齐。稳态落后 1 天，**零洞**。

    **0 行不是必然故障**：窗口整段落进「未发布尾区」（`UTC今日 - start ≤ _CRYPTO_TAIL_GRACE`）
    时，0 行是**正常等待**——标 `log_status='skipped'` 而非 `failed`（否则每次调度都刷一条
    假告警，真故障会被淹没）。窗口更长却 0 行＝真异常，照旧进 `failed_dates`。

    **partial 冻结（批 105）**：二轮补拉后**仍有**失败标的 ⇒ `freeze_on_partial=True` 的
    同步（**日线档**）游标**退回窗口起点前一日**（下限 `end - _PARTIAL_FREEZE_MAX_DAYS`，
    防 ratchet），下一轮整窗重拉自动补缺。动机：102b 首跑 7/485 标的失败而游标照推 ⇒
    首跑 30 天窗**永久缺失**（下轮只补 1 天）。日线档 cron 日频，冻结最多一天一重试，
    无风暴；**分钟/小时档保持无条件推进**（G-S1：高频 beat × 冻结＝整窗重拉风暴，
    宁可窗口洞靠告警暴露）。backfill 不冻结（sync() 对回补本就不推游标）。
    """
    def _handler(cfg: dict, end_date: str, backfill_from: str | None = None,
                 progress_cb: Callable | None = None) -> dict:
        from datetime import datetime, timezone

        from src.data_platform.db import save_bars, save_bars_overwrite
        adapter = _get_supply_adapter(cfg)
        end = _crypto_end(end_date, adapter, kind)
        start, _fam_excl = _crypto_base_start(cfg, backfill_from, adapter, kind)
        start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        if start > end:
            return {"pulled": 0, "saved": 0, "start": start_s, "failed_dates": [],
                    "expected_days": 0, "actual_days": 0, "cursor_upto": end_s}

        syms = adapter.list_symbols()
        if not syms:
            # 符号枚举失败必须**响亮**：静默返回 0 行会让 sync() 记 success 并推进游标
            # （＝把整段窗口的数据洞掩埋）。raise 走 sync() 的 except → error + 游标不动 + 告警。
            raise RuntimeError(f"{label} 符号枚举为空（{enum_hint}）")
        # 批 108·步 2（裁定 A）：标的身份/生命周期入 SM——**在拉取前**写，与拉取成败无关。
        # 元数据独立于数据；且**对账需要期望集**：某标的整窗 0 行时 SM 仍须有它（否则看不见）。
        # fail-soft（_sm_upsert 内部吞异常记 warning），不阻断主同步。
        _sm_upsert_crypto(adapter, syms)
        total = len(syms)
        pulled = saved = 0
        failed: dict[str, str] = {}   # sym → 首轮错误（dict 便于二轮补拉按标的清账）
        reached = ""      # 实际取到数据的最后一日（游标真值，见上方契约）
        # 回补用 overwrite：本地可能已存不完整/旧值，手动回补优先级最高（同 db.save_bars_overwrite 立法）
        write = save_bars_overwrite if backfill_from else save_bars
        # 排除段登记（§5.3）：per-symbol inception 才产出 → 按 (symbol, kind, from, to) 去重
        excl: dict[str, dict] = {
            f"{e.get('symbol', '')}|{e['kind']}|{e['from']}|{e['to']}": e for e in _fam_excl}

        def _start_of(sym: str) -> date:
            """per-symbol 下界 ＝ `max(家族起点, inception(sym))`（设计 §5.1「标的×日」）。

            inception 未知（裁定 F）⇒ 家族起点（**不**用源可达性冒充上币日）。
            顺带把该标的的 `policy_discard`/`unreachable` 段登记收上来。

            批 110：`_window_floors` 的 `sym` 语义＝ **`vt_symbol`**（SM 按该键精确查 inception；
            `sm_inception` 是裁定 G① 的唯一可读点）。此处 `sym` 是**源侧形态**（`list_symbols()`
            的返回），须先 `_vt_of_source` 转换——两者是不同键空间（`BTCUSDT` vs `BTCUSDT.BINANCE`）。
            """
            vt = _vt_of_source(sym, getattr(adapter, "venue", None))
            f, ex = _window_floors(adapter, kind, cfg, sym=vt)
            for e in ex:
                excl.setdefault(f"{e.get('symbol', '')}|{e['kind']}|{e['from']}|{e['to']}", e)
            return max(start, f) if f else start

        def _pull_one(sym: str) -> None:
            nonlocal pulled, saved, reached
            s0 = _start_of(sym)
            if s0 > end:
                return        # 该标的下界晚于窗口上界（如新上币）⇒ 本轮无作业，非失败
            df = adapter.fetch_supply(kind, sub_kind, symbol=sym,
                                      start=s0.strftime("%Y%m%d"), end=end_s, freq=freq)
            if df is not None and not df.empty:
                rows = adapter.to_bar_rows(df, freq)
                if rows:
                    pulled += len(rows)
                    saved += write(freq, rows)
                    day = max(r[2] for r in rows)      # r[2]=ts（UTC aware）
                    ds = day.strftime("%Y%m%d")
                    if ds > reached:
                        reached = ds

        for i, sym in enumerate(syms, 1):
            try:
                _pull_one(sym)
            except Exception as e:
                failed[sym] = f"{type(e).__name__}:{str(e)[:40]}"
            if progress_cb:
                progress_cb(i, total, sym)

        # 批 105 二轮补拉：首轮失败标的**立即**各重试一次（不 sleep——瞬时抖动大多秒级自愈，
        # 而 sleep 会把全任务时长乘进去；adapter 内部已有 2/4/8s 退避兜着间隔）。成功即清账。
        if failed:
            for sym in list(failed):
                try:
                    _pull_one(sym)
                    failed.pop(sym, None)
                except Exception as e:
                    failed[sym] = f"{type(e).__name__}:{str(e)[:40]}"

        # 游标契约（批 105 扩展，逐字见工厂 docstring「partial 冻结」段）：
        # 无残留失败 ⇒ 实际取到数据的最后一日（批 101b 契约不变）；
        # 有残留失败且 freeze_on_partial 且非 backfill ⇒ 冻结回窗口起点前一日（带 ratchet 下限）。
        if failed and freeze_on_partial and not backfill_from:
            freeze_floor = end - timedelta(days=_PARTIAL_FREEZE_MAX_DAYS)
            cursor = (max(start, freeze_floor) - timedelta(days=1)).strftime("%Y%m%d")
        else:
            cursor = reached or (start - timedelta(days=1)).strftime("%Y%m%d")
        log_status = None
        if pulled == 0 and not failed:
            if (datetime.now(timezone.utc).date() - start).days <= _CRYPTO_TAIL_GRACE:
                log_status = "skipped"    # 整窗未发布＝正常等待（上游 T+1 滞后），非故障
            else:
                # 「全窗口 0 行」但窗口足够长 ⇒ 异常态（上游不可达 / 路径变更）——勿静默成功
                failed["no_rows"] = "窗口内 0 行（上游不可达 / T+1 未落盘 / 窗口压空）"
        failed_list = [f"{sym}:{err}" for sym, err in sorted(failed.items())]
        logger.info("%s %s %s~%s：%d 标的，拉 %d 行，存 %d 行，失败 %d（游标 %s）",
                    label, sync_id, start_s, end_s, total, pulled, saved, len(failed_list), cursor)
        out = {"pulled": pulled, "saved": saved, "start": start_s,
               "failed_dates": failed_list, "expected_days": None,
               "actual_days": total - len(failed_list), "cursor_upto": cursor}
        if log_status:
            out["log_status"] = log_status
        if excl:
            # §5.3：排除段**必须登记**（policy_discard＝主动不保留 / unreachable＝源不可达）——
            # 它们**不是缺口**，绝不进 failed_dates（否则误触发告警）。落表 sync_gap 属批 109。
            out["excluded"] = sorted(excl.values(), key=lambda e: (e["kind"], e["from"]))
            logger.warning("%s %s：排除段登记 %d 条 %s（非缺口；批 109 落 sync_gap）",
                           label, sync_id, len(out["excluded"]),
                           [(e["kind"], e["from"], e["to"]) for e in out["excluded"][:5]])
        return out

    _handler.__name__ = f"_sync_{sync_id}"
    return _handler


_BINANCE_ENUM_HINT = "S3 list 不可达或返回异常"
_sync_binance_perp_daily = _make_crypto_bar_handler(
    "binance_perp_daily", *_BINANCE_BAR_SPECS["binance_perp_daily"],
    label="binance", enum_hint=_BINANCE_ENUM_HINT,
    freeze_on_partial=True)      # 批 105：日线档 partial 冻结（分钟/小时档不冻结＝G-S1）
_sync_binance_perp_hourly = _make_crypto_bar_handler(
    "binance_perp_hourly", *_BINANCE_BAR_SPECS["binance_perp_hourly"],
    label="binance", enum_hint=_BINANCE_ENUM_HINT)
_sync_binance_perp_1min = _make_crypto_bar_handler(
    "binance_perp_1min", *_BINANCE_BAR_SPECS["binance_perp_1min"],
    label="binance", enum_hint=_BINANCE_ENUM_HINT)
_sync_binance_perp_15min = _make_crypto_bar_handler(
    "binance_perp_15min", *_BINANCE_BAR_SPECS["binance_perp_15min"],
    label="binance", enum_hint=_BINANCE_ENUM_HINT)

# ── 批 102b：OKX 永续日线（同一条 crypto bar 链，仅 adapter 与 label 不同） ──
# 首跑 30 天与币安日线同档（「功能验证档」的量级；全史走 backfill_from，下界待 prod 实测后
# 才写进 sync_config.retention——**当前 NULL**，见迁移 0136/0139 的说明）。
_sync_okx_perp_daily = _make_crypto_bar_handler(
    "okx_perp_daily", "bar_daily", "perp", "1D",
    label="okx", enum_hint="instruments 接口不可达或返回异常（含代理出口未配/不通）",
    freeze_on_partial=True)      # 批 105：日线档 partial 冻结（102b 首跑 7/485 缺窗的直接教训）


# ═══════════════════════════════════════════════════════════════════════════════
# 批 109：分族对账 ＋ **输出闭环**（设计 §7.1 分族治法 / §7.2 输出闭环 / §5.3 / §5.4）
#
# 母设计双盲审① P1：**对账若只产报告、无消费者＝等于没做**。本节的每个输出都指名消费者：
#   - per-date（主路径）：同步收尾做**日期级差集** → **内联重拉** → 仍缺 ⇒ 落 `sync_gap('open')`
#     ＋ 聚合告警（消费者＝`sync()` 的日志/告警）；
#   - per-symbol（旁路）：标的级差集 → 落表 ＋ 告警 ＋ **限频重拉**（beat 周日 03:33）；
#   - `excluded`（批 108 只记日志的 `policy_discard`/`unreachable`）：升级为**落表终态**。
#
# 🔴 **`sync_gap` 是派生视图，不是第二真源**（设计 §7.2 明写）：真源＝数据表本身 ＋ 生命周期
#   主档（SM）＋ 窗口边界规则。⇒ **禁止**任何窗口/期望集计算读 `sync_gap`——禁读闸＝
#   `tests/test_batch109_sync_gap.py::TestNoBackflowGate`（同文件内含**跨模块符号泄漏**扫）。
# ═══════════════════════════════════════════════════════════════════════════════

# 五态（与迁移 0140 的 CHECK 同源）。`open` ＝ 已检出未补齐的**合法持续态**；终态四态 ＝
# **不再重拉**的出口（`closed` 补齐 / `unreachable` 源不可达 / `policy_discard` 主动不保留 /
# `uncertain` 期望集不可信）。「存量缺口」指标 ＝ `state='open'` 行数（§7.2）。
SYNC_GAP_STATES: tuple[str, ...] = ("open", "closed", "unreachable", "policy_discard", "uncertain")
# 非终态＝视图式同步「集合差」的作用域：`open` 待补 ＋ `uncertain` **可恢复**（复审 R1：
# 覆盖恢复后必须能翻正，否则 fresh 库首跑全 uncertain 后成历史永久盲区）
_SYNC_GAP_LIVE_STATES: tuple[str, ...] = ("open", "uncertain")
# 两类语义**互不覆盖**（撞同一锚 ⇒ 只告警不改写：「不得静默」）——缺口主张 vs 排除段登记
_SYNC_GAP_CLAIM_STATES: tuple[str, ...] = ("open", "uncertain")
_SYNC_GAP_EXCLUDED_STATES: tuple[str, ...] = ("unreachable", "policy_discard")

# 快照 age 阈值（自然日）：SM 列表快照超此 age ⇒ 期望集不完整 ⇒ `uncertain(reason='stale_source')`
# （设计 §5.4 第一行；§九.1「依赖倒置」的闭合口——inception 是派生数据，快照过期即系统性漏报）。
_SNAPSHOT_STALE_DAYS = 7
# per-symbol 限频重拉：`open` 行 pull_count < 此值 **且** 距 `last_seen` ≥ 1 天 ⇒ 按缺口区间重拉
_GAP_REPULL_MAX = 3
_GAP_REPULL_MIN_INTERVAL_DAYS = 1
# 聚合告警样本段数（整轮至多 1 条，只对**本轮新 open** 响铃——防首跑全史场景告警风暴）
_SYNC_GAP_ALERT_SAMPLE = 5

# per-symbol 对账范围（旁路低频）。**只纳「缺＝预期日无数据」可判定的时序族**（设计 §5.2）：
#   sync_id -> (kind, sub_kind, freq, table, 日期时区, SM category)
# ⚠️ `pool_data`（income/balancesheet/cashflow/股东族）**不纳**——其「缺」不是「预期日无数据」
#   （按公告日不定期到达、无逐日期望）⇒ 日期级差集对它**不适用**，强纳即产**假缺口**。
#   设计 §7.1 的枚举带了它，但 §5.2 的「缺的定义」在判据层级上更先——本批按 §5.2 收窄，
#   差异记入任务文件（Q6 待裁）。
_RECONCILE_SYMBOL_SCOPE: dict[str, tuple[str, str | None, str, str, str, str]] = {
    "binance_perp_daily": ("bar_daily", "perp", "1D", "bar_1d", "UTC", "perp"),
    "binance_perp_hourly": ("bar_minute", "perp", "1h", "bar_1h", "UTC", "perp"),
    "binance_perp_1min": ("bar_minute", "perp", "1min", "bar_1min", "UTC", "perp"),
    "binance_perp_15min": ("bar_minute", "perp", "15min", "bar_15min", "UTC", "perp"),
    "okx_perp_daily": ("bar_daily", "perp", "1D", "bar_1d", "UTC", "perp"),
    "astock_minute": ("bar_minute", None, "1min", "bar_1min", "Asia/Shanghai", "stock"),
    "astock_minute_5min": ("bar_minute", None, "5min", "bar_5min", "Asia/Shanghai", "stock"),
}


# ═══════════════════════════════════════════════════════════════════════════════
# 批 110：**对账判据地基**（母设计 §八.5 的门控判据从散文变可判定；方案 v3）
#
# 三条并列目标（缺一不算成功）：① 每轮落结构化摘要 ⇒ 判据**可自动判定**；② 成本由 **scope 数**
# 决定、不由数据量决定（真批量）；③ 「哪些 scope 的 inception 真值可读」是**显式声明**且被
# 互证闸与真实可及性**互证**。**本批不删冻结**（R0：判据四者皆缺时删＝摘安全网）。
# ═══════════════════════════════════════════════════════════════════════════════

# 判据「**连续 N 轮**同缺口」的 N（R3 定值）：周频 ⇒ 4 轮 ≈ 1 个自然月，覆盖 `publish_lag`
# 迟发布与周末/节假日两类边界。**落点＝代码常量**（母设计 §三「同类任务共享 ⇒ 代码按 kind 给默认」；
# §四 DB 承载物清单**不含**任何判据阈值）——但**必须进可观测摘要**（N7），否则判据不可复核。
_GAP_JUDGE_ROUNDS: int = 4

# 显式声明「inception 真值**结构性不可得**」的 scope（裁定 F）。当前＝4 个 binance perp scope
# （`fapi` 被墙 ⇒ `onboardDate` 不可得 ⇒ SM `list_date` 结构性 NULL，事实 6/9）。
# **互证闸**（`_reconcile_symbols`）在 SM 快照**新鲜**时比对「有 symbol 且 `list_date` 空」的
# **实测集**与该**声明集**：不等 ⇒ 声明失真（可能是「一条路径现已可及」或「快照抖动」）⇒ 告警。
# ⚠️ 只在新鲜时评估（stale ⇒ `unverified`，**不判**）——避免把「瞬时探不到」误判为结构性未知。
_RECONCILE_UNKNOWN_INCEPTION: frozenset[str] = frozenset({
    "binance_perp_daily", "binance_perp_hourly", "binance_perp_1min", "binance_perp_15min",
})

# 本轮该 scope 的**落表行数**上界（**按 kind/family 给**——daily 与 minute 量级差三四个数量级，
# 单值必致「minute 恒漏报」或「daily 无保护」）。**语义钉死＝落表行数**（**不**约束扫描量——扫描
# 量须先读才知，不可预算）。超阈 ⇒ **不落该 scope 的缺口主张** ＋ 告警 ＋ 摘要标 `budget_exceeded`。
# ⚠️ **显名取舍：用漏报换稳定**——该 scope 当轮缺口不报，以**告警**替代**静默**；且其判据
# **不许过门**（与覆盖率<1 同制）。astock_minute 族一旦启用，`bar_1min` 每标行数远大于日线
# ⇒「启用前先评估对账体量」是前置条件。
_GAP_ROW_BUDGET: dict[str, int] = {"bar_daily": 5_000, "bar_minute": 50_000}

# SM 覆盖率「**长期**」低（≥ 此连续轮数）⇒ 响亮告警（v3 V7）：防「一次刷新网络抖动写 `None`
# 且**未恢复**」使缺口被 §5.4 **永久静默抑制**（抑制本是安全网，失修即变盲区）。
_SM_COVER_STALE_ROUNDS: int = 2

# 轮次身份（严格单调）的 Valkey 键：`INCR` ⇒ 每轮入口无条件推进一次。
_ROUND_KEY = "reconcile:round"
# 覆盖率低轮次计数的 Valkey 键前缀（跨轮「连续」计数）。
_COVER_LOW_KEY = "reconcile:cover_low"


def _vt_of_source(sym: str, venue: str | None) -> str | None:
    """源侧符号 ＋ venue → `vt_symbol`（与 `_sm_upsert_crypto` **同源拼法**）；venue 缺失 ⇒ `None`。

    crypto 的 `list_symbols()` 返回**源侧形态**（binance `BTCUSDT`／okx `BTC-USDT-SWAP`），
    而 SM 主键是 `vt_symbol`（`*.BINANCE`／`*.OKX`）——两者是不同键空间，**必须显式转换**
    （`sm_inception` 按 vt_symbol 精确查，不做模糊匹配：模糊匹配＝第二真源）。
    """
    if not venue:
        return None
    s = str(sym)
    return s if s.upper().endswith("." + venue) else f"{s}.{venue}"


def _next_round_id() -> int | None:
    """本轮对账的**轮次身份**（严格单调）：Valkey `INCR reconcile:round`。不可用 ⇒ `None`。

    **为什么必须是「无条件推进的序号」而非墙钟**（v3 V1 核心）：
    ① 裸 Unix 秒在 beat **周频**下手动重跑**可达同秒** ⇒ 碰撞（双计/误判）；
    ② NTP 回拨可非单调；
    ③ DB `MAX(last_hit_round)+1` 派生在「该轮**所有** scope 均未写行」（全族跳过）时**不推进**
       ⇒ 断档被当连续（**假过门**）。
    Valkey 的**失效方向安全**：丢 ⇒ 计数回退 ⇒ `last_hit_round != round_id-1` ⇒ **复位** ⇒
    只推迟过门，不产生假过门。**不可用 ⇒ `None`** ⇒ 该轮**只观测不判定**（判据不过门）＋ 摘要标注。
    """
    try:
        from src.quant_common.redis_client import business_redis
        return int(business_redis(socket_timeout=5, socket_connect_timeout=5).incr(_ROUND_KEY))
    except Exception as e:
        logger.warning("对账轮次 id 不可得（Valkey）⇒ 本轮只观测不判定: %s", e)
        return None


def _bump_cover_low(sync_id: str, low: bool, round_id: int | None) -> int | None:
    """覆盖率低的**连续轮次**计数（Valkey，跨轮）。`low` ⇒ 累加；否则清零。不可用 ⇒ `None`。

    ⭐ **断档复位（批 110 步 4 盲审 P1-5，与 `hit_rounds` 同构）**：只有
    `last_cover_round == round_id-1`（上一**评估**轮就是上一轮）才累加，否则**归 1**。

    为什么不能只靠「不低时清零」（原实现）：该 scope **整轮未被评估**时（未配置/已禁用 ⇒
    `continue`；V6「SM 无该族 symbol」⇒ `continue`）本函数**根本不被调用** ⇒ 计数被**冻结**
    ⇒ 下次评估跨过未评估轮继续累加 ⇒ `streak >= _SM_COVER_STALE_ROUNDS` 被**跨过断档**满足
    ⇒ **假告警**。这正是 V1 立项的同一反例（「中间整轮跳过 ⇒ 断档被当连续」）在 V7 上的复发；
    状态标记必须连**维护语义（何时累加/何时复位）**一起写死——字段存在 ≠ 判据闭合。

    `round_id is None`（Valkey 不可用）⇒ 不维护、不告警（返回 `None`，失效方向＝少报）。
    """
    if round_id is None:
        return None
    try:
        from src.quant_common.redis_client import business_redis
        r = business_redis(socket_timeout=5, socket_connect_timeout=5, decode_responses=True)
        key = f"{_COVER_LOW_KEY}:{sync_id}"
        last_key = f"{_COVER_LOW_KEY}:{sync_id}:last_round"
        if not low:
            r.delete(key)
            r.delete(last_key)
            return 0
        prev = r.get(key)
        last = r.get(last_key)
        streak = (int(prev) + 1) if (prev is not None and last is not None
                                     and int(last) == round_id - 1) else 1
        r.set(key, streak)
        r.set(last_key, round_id)
        return streak
    except Exception as e:
        logger.warning("覆盖率低计数不可得（Valkey，%s）: %s", sync_id, e)
        return None


def _gap_judge(stats: dict, scope_rows: list[tuple], round_id: int | None,
               *, suppressed: bool = False) -> dict:
    """判据计算（**纯函数，无 IO**）：本轮**误报证据**（R2）＋ **连续命中分布**（R3）。

    返回 `{"hit_ge_n": int, "false_evidence": {...}, "by_anchor": [...]}`。

    `stats` ＝ 本轮该 scope 的聚合统计（`opened/reopened/closed/new/uncertain_*`）；
    `scope_rows` ＝ 本轮写完后该 scope 的行 `(symbol, gap_start, state, hit_rounds, last_hit_round)`。

    **R2「零误报」只覆盖可自动判定的两类**（**P-2 已删**——它会把主同步补齐后的**正常
    `closed`** 误判为误报）：
      - **P-1 同轮振荡**：同一锚本轮既 `opened` 又 `closed`（自相矛盾）；
      - **P-3 抑制失效**：`uncertain` 的本轮仍产出 `open`/`new`（代码不应到达）。
    ⚠️ **诚实边界（第四类判不了）**：「主张了不存在的缺口」且**行为自洽**的误报，本判据**看不见**
    ——须靠**金标准对照**（批 105 的 OKX 7 标的）在步 8 集成观察里核。本函数**不**假装能判它。

    **判定式**：`hit_rounds >= _GAP_JUDGE_ROUNDS`（**复位已自证连续**——`_sync_gap_sync` 维护时
    `last_hit_round == round_id-1` 才累加，否则归 1；故 `== 本轮` 冗余，仅作摘要断言）。

    ⭐ **门控合取（批 110 步 4 盲审 P1-1；落地方案 §产出B「`budget_exceeded` 的 scope 其判据
    不许过门（与覆盖率<1 同制）」）**：`suppressed=True` ⇒ **本轮不构成「命中」** ⇒ `hit_ge_n=0`
    且 `judged=False`。取值面＝**该轮本轮主张被抑制**：
      - `budget_exceeded`（`:2364` 把 `gaps_map` 全置 `None`）——此时行**保持 `open` 且
        `hit_rounds` 保留旧值**，故不显式归零就会「拿旧值过门」；
      - `sm_covered < sm_total`（部分标的 `inception` 结构性未知、被 §5.4 抑制）。
    （`round_id is None`（Valkey 不可用）与 `stale_source` 两路**已天然闭合**：前者由
    `round_id is not None` 条件，后者因全族转 `uncertain` ⇒ 无 `open` 行可数——不必入 `suppressed`。）

    ⚠️ **诚实边界（R2 的实测修正，批 110 步 4 盲审 C3/D1）**：`false_evidence` 两类（P-1/P-3）
    在 `_sync_gap_sync` 的**单分支结构**下**结构不可达**（同一 `(symbol, anchor)` 一轮内只走
    opened/closed/uncertain **之一**）⇒ 它们是**防御性哨兵**（防将来重构引入该缺陷），
    **不构成自动误报门**。「零误报」的实质核验在**第四类金标准对照**（步 8 集成观察）。
    """
    # 三集**必须同形态**（`(symbol, anchor)`）：`new` 是 `(sym, a, e)`、`closed_anchors`/
    # `uncertain_anchors` 是 `(sym, a)`。若 `opened` 取裸锚 `a`，则 `opened & closed` 与
    # `uncertain & opened` **恒为空集**（str vs tuple 永不相等）⇒ 两类误报检测器**静默失效**
    # （判据绿而实无保护）。批 110 自查由 `TestFalseEvidence` 抓出。
    opened = {(s, a) for s, a, _e in (stats.get("new") or [])}
    closed = set(stats.get("closed_anchors") or [])
    uncertain = set(stats.get("uncertain_anchors") or [])
    by_anchor = [{"symbol": r[0], "gap_start": r[1], "state": r[2],
                  "hit_rounds": r[3], "last_hit_round": r[4]} for r in scope_rows]
    blocked = round_id is None or suppressed
    hit_ge_n = 0 if blocked else sum(
        1 for r in scope_rows
        if r[2] == "open" and int(r[3] or 0) >= _GAP_JUDGE_ROUNDS)
    return {
        "hit_ge_n": hit_ge_n,
        "judged": not blocked,
        "suppressed": bool(suppressed),
        "false_evidence": {
            # P-1：同轮振荡（同锚既 opened 又 closed）
            "same_round_oscillation": sorted(opened & closed),
            # P-3：抑制失效（uncertain 的本轮仍产主张）
            "suppress_leak": sorted(uncertain & opened),
        },
        "by_anchor": by_anchor,
    }


def _gap_span(spans: list[str], sample: int = _SYNC_GAP_ALERT_SAMPLE) -> str:
    """告警/日志用短串：前 N 项 + 其余计数（整轮聚合，不逐条刷）。"""
    head = "; ".join(spans[:sample])
    return head + (f" …(+{len(spans) - sample})" if len(spans) > sample else "")


def _to_segments(missing: list[str], ordered_expected: list[str]) -> list[tuple[str, str]]:
    """缺日 → **连续段**（按**期望集顺序**合并：期望集相邻 ⇒ 同段，非交易日不切开）。

    与 `_find_gaps` 同构但**不沿用其语义**：`_find_gaps` 从「本地首日」起扫（对**整窗 0 行**的
    标的天然失明），对账必须从**窗口下界**起扫——整窗 0 行的标的正是要抓的对象。
    """
    if not missing:
        return []
    idx = {d: i for i, d in enumerate(ordered_expected)}
    pos = sorted(idx[d] for d in missing if d in idx)
    if not pos:
        return []
    segs: list[tuple[str, str]] = []
    s = p = pos[0]
    for cur in pos[1:]:
        if cur == p + 1:
            p = cur
            continue
        segs.append((ordered_expected[s], ordered_expected[p]))
        s = p = cur
    segs.append((ordered_expected[s], ordered_expected[p]))
    return segs


def _local_dates(table: str, date_expr: str, where: str = "",
                 params: tuple = ()) -> set[str] | None:
    """本地已有日期集合（**对账的唯一读取口**）。不可判 ⇒ `None`（fail-closed ⇒ uncertain）。

    `None` 的来源：表不存在（未建/未上产）、查询失败、连接异常。**不得**退化成空集——空集会被
    当成「一天都没有」⇒ 全窗口假缺口。对账侧 fail-closed，与拉取侧的 fail-open 相对（§5.4）。
    """
    sql = f"SELECT DISTINCT {date_expr} FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        with get_conn() as conn:
            cur = conn.execute(sql, params)
            return {r[0] for r in cur.fetchall() if r[0]}
    except Exception as e:
        logger.warning("对账本地日期读取失败（table=%s）⇒ 该 scope 转 uncertain: %s", table, e)
        return None


def _local_dates_map(table: str, date_expr: str, symbols: list[str],
                     ) -> dict[str, set[str]] | None:
    """**scope 级一次**取本地日期集：`{symbol: {date}}`（**读取规模契约**：不逐标的往返）。

    与 `_local_dates(table, date_expr, "symbol=%s", (sym,))` **语义等价**（后者是单标的 SQL 版，
    仍是 per-date 族的口）——等价性由 `test_batch110_reconcile_scale.TestBatchEquivalence` 钉住：
    同一表同一表达式下，`_local_dates_map(...).get(sym, set())` 必须逐值等于单标的 `_local_dates`。

    不可判 ⇒ `None`（fail-closed ⇒ **整个 scope** 转 `uncertain`）——与 `_local_dates` 同纪律：
    不得退化成「空集」（那会被当成「一天都没有」⇒ 全窗口假缺口）。
    """
    if not symbols:
        return {}
    sql = f"SELECT symbol, {date_expr} FROM {table} WHERE symbol = ANY(%s)"
    try:
        with get_conn() as conn:
            cur = conn.execute(sql, (list(symbols),))
            out: dict[str, set[str]] = {}
            for s, d in cur.fetchall():
                if d:
                    out.setdefault(s, set()).add(d)
            return out
    except Exception as e:
        logger.warning("对账本地日期批量读取失败（table=%s）⇒ 该 scope 转 uncertain: %s", table, e)
        return None


def _upsert_sync_gap(rows) -> dict:
    """写侧**唯一入口**（终态排除段 / 通用单行写）：读-改-写幂等，**不用 `ON CONFLICT` 盲合并**。

    `rows` ＝ 迭代 `(sync_id, symbol, gap_start, gap_end, state, reason)` 六元组。**语义由调用方
    给定**（本函数不做推断——单一职责：落表）。返回计数 `{inserted, reopened, updated, skipped}`。
    """
    stats = {"inserted": 0, "reopened": 0, "updated": 0, "skipped": 0}
    rows = list(rows or [])
    if not rows:
        return stats
    with get_conn() as conn:
        cur = conn.cursor()
        for sync_id, symbol, gap_start, gap_end, state, reason in rows:
            if state not in SYNC_GAP_STATES:
                raise ValueError(f"sync_gap 非法 state={state!r}（合法：{SYNC_GAP_STATES}）")
            stats[_gap_put(cur, sync_id, symbol or "", str(gap_start), str(gap_end),
                           state, reason)] += 1
        conn.commit()
    return stats


def _gap_put(cur, sync_id: str, symbol: str, gap_start: str, gap_end: str,
             state: str, reason: str | None) -> str:
    """写一行（**读-改-写**）。返回 `inserted|reopened|updated|skipped`。

    - 无行 ⇒ INSERT；
    - 同态 ⇒ 只刷 `gap_end/last_seen`；
    - 异态**跨类**（缺口主张 ↔ 排除段登记）⇒ **只告警不改写**：两类语义撞同一锚说明至少一方
      的假设已破，静默覆盖会丢掉任一方的可见性（「不得静默」）；
    - 异态同类 ⇒ 改写；`closed` 再现 ⇒ **重开＝新缺口事件**（`pull_count=0`、`first_seen=now()`、
      `closed_at=NULL`）——真源是数据表，`sync_gap` **不承诺事件历史**。
    """
    cur.execute("SELECT state FROM sync_gap WHERE sync_id=%s AND symbol=%s AND gap_start=%s",
                (sync_id, symbol, gap_start))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO sync_gap (sync_id, symbol, gap_start, gap_end, state, reason) "
            "VALUES (%s,%s,%s,%s,%s,%s)", (sync_id, symbol, gap_start, gap_end, state, reason))
        return "inserted"
    prev = row[0]
    if ((prev in _SYNC_GAP_CLAIM_STATES and state in _SYNC_GAP_EXCLUDED_STATES)
            or (prev in _SYNC_GAP_EXCLUDED_STATES and state in _SYNC_GAP_CLAIM_STATES)):
        logger.warning("sync_gap 锚冲突（%s/%s/%s）：既有 %s vs 本次 %s —— 不改写"
                       "（缺口主张与排除段登记互不覆盖）", sync_id, symbol, gap_start, prev, state)
        return "skipped"
    reopened = prev == "closed"
    cur.execute(
        "UPDATE sync_gap SET gap_end=%s, state=%s, reason=%s, last_seen=now(), closed_at=NULL, "
        "first_seen=CASE WHEN %s THEN now() ELSE first_seen END, "
        "pull_count=CASE WHEN %s THEN 0 ELSE pull_count END "
        "WHERE sync_id=%s AND symbol=%s AND gap_start=%s",
        (gap_end, state, reason, reopened, reopened, sync_id, symbol, gap_start))
    return "reopened" if reopened else "updated"


def _anchor(x) -> str:
    """缺口锚点/区间端点 → **唯一形态 `YYYY-MM-DD`**（对账边界归一）。

    对账层内部日期键在**算侧**统一是紧凑 `%Y%m%d`（`_trade_dates_in_range` / `to_char(...,'YYYYMMDD')`
    / `strftime("%Y%m%d")` 三处同源），而**读侧**从 `sync_gap DATE` 列读回 `date` 后经 `.isoformat()`
    带连字符 ⇒ 两者在同一集合里做差集**永不相等**，会把「既有行」误判为「新锚」（假 closed + 假
    open + 假告警）。故在 `_sync_gap_sync` 边界一律归一，使调用方用哪种写法都安全（per-date /
    per-symbol 两族与单测共用此口）。
    """
    return _ymd(x).isoformat()


def _sync_gap_scope_rows(sync_id: str, symbols: list[str]) -> dict[str, list[tuple]]:
    """**一次**读该 scope 的**全部** `sync_gap` 行（含终态）——视图式同步与重拉候选的共同读取口。

    返回 `{symbol: [(gap_start, gap_end, state, pull_count, last_seen, hit_rounds, last_hit_round)]}`
    （日期已 `_anchor` 归一）。

    **为什么读全部状态而非只读非终态**（批 110）：新起点判定要知道「该锚是否落在 `closed` 行上」
    （⇒ reopen 而非 INSERT）或「是否撞排除段登记」（`unreachable`/`policy_discard` ⇒ 只告警不改写）。
    只读非终态会把这两种情形误判为 INSERT ⇒ 撞 UNIQUE（`sync_gap_ident`）而**响亮炸**，或静默
    丢掉排除段可见性。
    """
    out: dict[str, list[tuple]] = {}
    if not symbols:
        return out
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT symbol, gap_start, gap_end, state, pull_count, last_seen, "
            "hit_rounds, last_hit_round FROM sync_gap "
            "WHERE sync_id=%s AND symbol = ANY(%s) ORDER BY symbol, gap_start",
            (sync_id, list(symbols)))
        for r in cur.fetchall():
            out.setdefault(r[0], []).append(
                (_anchor(r[1]), _anchor(r[2]), r[3], r[4], r[5], r[6], r[7]))
    return out


def _repullable_map(prev_map: dict[str, list[tuple]]) -> dict[str, list[tuple[str, str]]]:
    """从 scope 行**内存**筛限频重拉候选：`open` 且 `pull_count < _GAP_REPULL_MAX` 且
    `last_seen` 距 now ≥ `_GAP_REPULL_MIN_INTERVAL_DAYS` 天。

    与 `_list_repullable`（单标的 SQL 版）**语义等价**——批量路径不逐标的查库（读取规模契约）。
    `(now - last_seen).days >= 1` ⇔ SQL 的 `last_seen <= now() - interval '1 day'`（正 timedelta
    的 `.days` 是 floor ⇒ 两者严格等价）。
    """
    from datetime import datetime as _dtm, timezone as _tzz
    now = _dtm.now(_tzz.utc)
    out: dict[str, list[tuple[str, str]]] = {}
    for sym, rows in (prev_map or {}).items():
        cand = [(r[0], r[1]) for r in rows
                if r[2] == "open" and (r[3] or 0) < _GAP_REPULL_MAX
                and r[4] is not None and (now - r[4]).days >= _GAP_REPULL_MIN_INTERVAL_DAYS]
        if cand:
            out[sym] = cand
    return out


def _sync_gap_sync(sync_id: str, *, symbols: list[str], win_map: dict[str, tuple[str, str]],
                   round_id: int | None = None,
                   gaps_map: dict[str, list[tuple[str, str]]] | None = None,
                   uncertain_map: dict[str, str] | None = None,
                   pulled_map: dict[str, list[str]] | None = None,
                   prev_map: dict[str, list[tuple]] | None = None) -> dict:
    """**视图式全量重同步（scope 级）**：以**本轮现算**为唯一真源，与 scope 内行做**集合差**。

    **读取规模契约（批 110 · 产出 D）**：live 行**一次**读出（`prev_map` 可复用调用方已读的口，
    避免重复往返），随后全程**内存集合运算**，最后**逐锚批量语句**（`executemany`）落库
    ⇒ 往返次数＝**常数**，与**标的数无关**（陈述与验收均不含 O(标的数)）。

    **禁 `ON CONFLICT` 盲合并**（迁移 0140 纪律）：盲合并会把旧行的 `pull_count/last_seen/
    hit_rounds` 带进无关的新缺口 ⇒ 污染「连续 N 轮同缺口」判据。

    唯一算子（设计 §7.2；步 2 双盲审 P0-1 修正、复审 R2 措辞统一；批 110 加**命中维护**）：
    ① 非终态行锚 ∈ 本轮缺口起点集 ⇒ `UPDATE gap_end=本轮现算 / last_seen` ＋ **命中累加/复位**
       （**整值对齐**，**不是**「收缩旧值」——字面「收缩」会回潮 P0-1 的幽灵 open）；
    ② 锚 ∉ ⇒ 置 `closed`（**保留** `hit_rounds/last_hit_round` 供判据读史）——**这同时是
       uncertain 的恢复路径**（复审 R1）；
    ③ 本轮有起点而 scope 无该锚行 ⇒ 无行 INSERT ／ `closed` 行 ⇒ **重开＝新事件**
       （`hit_rounds=1`、`pull_count=0`、`first_seen=now()`、`closed_at=NULL`）／
       排除段登记（`unreachable`/`policy_discard`）⇒ **只告警不改写**（两类语义互不覆盖）。

    **命中维护语义（批 110 核心 · 断档复位）**：
      - 新开/reopen ⇒ `hit_rounds=1`、`last_hit_round=round_id`；
      - 本轮对齐 ⇒ `hit_rounds = hit_rounds+1 if last_hit_round == round_id-1 else 1`；
      - `closed` ⇒ **保留**（判据读史）。
      **为什么必须复位**：`hit_rounds>=N 且 last_hit_round==本轮` **只证「最近一次命中＝本轮」，
      不证「最近 N 轮连续」**。反例：轮 1–4 命中 → 轮 5 该 scope **整轮跳过**（行不动）→ 轮 6–7
      命中 ⇒ `hr=6` 判过门，**实际连续仅 2 轮**。复位（`== round_id-1` 才累加）把它归 1 ⇒ 正确不过门。

    `uncertain_map[sym]` 非空 ⇒ §5.4 **抑制一切缺口主张**：既有非终态行统一转 `uncertain`
    （保留各自区间＝「这段以前判为缺，现在无法判定」）；无行 ⇒ 落一条 scope 级 `uncertain`
    （区间＝`win_map[sym]`）。`gaps_map` 为 `None`、或某 sym **不在其中** ⇒ 该 sym 本轮**未现算**
    （响亮告警 ＋ 只刷 `last_seen`）；`gaps_map[sym] is None` ⇒ 显式「本轮无作业/不主张」（静默）。
    `round_id is None`（Valkey 不可用）⇒ **只观测不判定**（不更新命中列）。

    返回聚合统计：`{opened, reopened, updated, closed, uncertain, new:[(symbol,a,e)],
    closed_anchors:[(symbol,a)], uncertain_anchors:[(symbol,a)]}`。
    """
    symbols = list(symbols or [])
    stats = {"opened": 0, "reopened": 0, "updated": 0, "closed": 0, "uncertain": 0,
             "new": [], "closed_anchors": [], "uncertain_anchors": []}
    if not symbols:
        return stats
    prev_map = prev_map if prev_map is not None else _sync_gap_scope_rows(sync_id, symbols)

    align: list[tuple] = []        # ① 对齐（含命中维护）
    align_nh: list[tuple] = []     # ① 对齐（round_id 不可用 ⇒ 不维护命中）
    clo: list[tuple] = []          # ② closed
    unc_u: list[tuple] = []        # uncertain（既有行转）
    unc_i: list[tuple] = []        # uncertain（无行 ⇒ 落 scope 级）
    ins: list[tuple] = []          # ③ INSERT
    reopen: list[tuple] = []       # ③ closed 再现 ⇒ 重开
    touch: list[tuple] = []        # 不主张 ⇒ 只刷 last_seen
    pull: list[tuple] = []         # 限频重拉尝试计数

    for sym in symbols:
        _w = win_map.get(sym) or ("", "")
        # 窗口端点**一律 `_anchor` 归一**（与既有行/现算集的键空间对齐）——否则 `unc_i` 的 INSERT
        # 会用紧凑 `20260927` 而既有行的 `gap_start` 读回是 `2026-09-27` ⇒ 锚不等 ⇒ 撞 UNIQUE
        # `sync_gap_ident`（这正是 `_anchor` docstring 说的「两者做差集永不相等」同族）。
        lo = _anchor(_w[0]) if _w[0] else ""
        hi = _anchor(_w[1]) if _w[1] else ""
        prev = prev_map.get(sym) or []
        by_anchor = {r[0]: r for r in prev}
        live = [r for r in prev if r[2] in _SYNC_GAP_LIVE_STATES]
        ureason = (uncertain_map or {}).get(sym)

        if ureason:
            if live:
                for r in live:
                    unc_u.append((ureason, sync_id, sym, r[0]))
                    stats["uncertain"] += 1
                    stats["uncertain_anchors"].append((sym, r[0]))
            else:
                # 无 live 行 ⇒ 该锚（`win_map` 的 `lo`）上可能已有**终态**行（`closed`/排除段）——
                # 必须**读-改-写**（`_gap_put` 语义），**不得**盲 INSERT（会撞 UNIQUE `sync_gap_ident`）。
                row = by_anchor.get(lo)
                if row is None:
                    unc_i.append((sync_id, sym, lo, hi, ureason))
                    stats["uncertain"] += 1
                    stats["uncertain_anchors"].append((sym, lo))
                elif row[2] in _SYNC_GAP_EXCLUDED_STATES:
                    logger.warning("sync_gap 锚冲突（%s/%s/%s）：既有 %s vs 本次 uncertain —— 不改写"
                                   "（缺口主张与排除段登记互不覆盖）", sync_id, sym, lo, row[2])
                else:
                    unc_u.append((ureason, sync_id, sym, lo))
                    stats["uncertain"] += 1
                    stats["uncertain_anchors"].append((sym, lo))
            continue

        if gaps_map is None or sym not in gaps_map:
            # 「本轮未现算」≠「本轮现算为空」：前者**不主张**（只刷 last_seen），后者才是「全补齐 ⇒
            # 逐行 closed」。把 None 当空集会**谎报 closed**（P0-1 同族）⇒ 响亮告警后原样返回。
            logger.warning("sync_gap 视图式同步收到未现算（⇒ 本轮不主张，%s/%s）", sync_id, sym)
            for r in live:
                touch.append((sync_id, sym, r[0]))
            continue
        gm = gaps_map[sym]
        if gm is None:
            # 显式「本轮无作业/不主张」（如 per-symbol 下界晚于窗口上界的新上币）⇒ 静默刷 last_seen
            for r in live:
                touch.append((sync_id, sym, r[0]))
            continue

        current = {_anchor(a): _anchor(e) for a, e in (gm or [])}
        live_anchors = {r[0] for r in live}
        for r in live:
            a = r[0]
            if a in current:
                if round_id is None:
                    align_nh.append((current[a], sync_id, sym, a))
                else:
                    align.append((current[a], round_id - 1, round_id, sync_id, sym, a))
                stats["updated"] += 1
            else:
                clo.append((sync_id, sym, a))
                stats["closed"] += 1
                stats["closed_anchors"].append((sym, a))
        for a in sorted(set(current) - live_anchors):
            row = by_anchor.get(a)
            hr = 1 if round_id is not None else 0
            if row is None:
                ins.append((sync_id, sym, a, current[a], hr, round_id or 0))
                stats["opened"] += 1
                stats["new"].append((sym, a, current[a]))
            elif row[2] == "closed":
                reopen.append((current[a], hr, round_id or 0, sync_id, sym, a))
                stats["reopened"] += 1
                stats["new"].append((sym, a, current[a]))
            else:
                # 排除段登记撞缺口主张 ⇒ **只告警不改写**（「不得静默」，两类语义互不覆盖）
                logger.warning("sync_gap 锚冲突（%s/%s/%s）：既有 %s vs 本次 open —— 不改写"
                               "（缺口主张与排除段登记互不覆盖）", sync_id, sym, a, row[2])
        for a in (pulled_map or {}).get(sym, []):
            pull.append((sync_id, sym, _anchor(a)))

    with get_conn() as conn:
        cur = conn.cursor()
        if align:
            cur.executemany(
                "UPDATE sync_gap SET gap_end=%s, state='open', reason=NULL, last_seen=now(), "
                "closed_at=NULL, "
                "hit_rounds=CASE WHEN last_hit_round=%s THEN hit_rounds+1 ELSE 1 END, "
                "last_hit_round=%s WHERE sync_id=%s AND symbol=%s AND gap_start=%s", align)
        if align_nh:
            cur.executemany(
                "UPDATE sync_gap SET gap_end=%s, state='open', reason=NULL, last_seen=now(), "
                "closed_at=NULL WHERE sync_id=%s AND symbol=%s AND gap_start=%s", align_nh)
        if clo:
            cur.executemany(
                "UPDATE sync_gap SET state='closed', closed_at=now(), last_seen=now() "
                "WHERE sync_id=%s AND symbol=%s AND gap_start=%s", clo)
        if unc_u:
            # `closed` 再现 ⇒ 按 `_gap_put` 语义**重开＝新事件**（`first_seen=now()`、`pull_count=0`）；
            # `open`/`uncertain` ⇒ 只翻态不动事件字段（CASE 读的是**旧行值**）。
            cur.executemany(
                "UPDATE sync_gap SET state='uncertain', reason=%s, last_seen=now(), closed_at=NULL, "
                "first_seen=CASE WHEN state='closed' THEN now() ELSE first_seen END, "
                "pull_count=CASE WHEN state='closed' THEN 0 ELSE pull_count END "
                "WHERE sync_id=%s AND symbol=%s AND gap_start=%s", unc_u)
        if unc_i:
            cur.executemany(
                "INSERT INTO sync_gap (sync_id, symbol, gap_start, gap_end, state, reason) "
                "VALUES (%s,%s,%s,%s,'uncertain',%s)", unc_i)
        if ins:
            cur.executemany(
                "INSERT INTO sync_gap (sync_id, symbol, gap_start, gap_end, state, reason, "
                "hit_rounds, last_hit_round) VALUES (%s,%s,%s,%s,'open',NULL,%s,%s)", ins)
        if reopen:
            cur.executemany(
                "UPDATE sync_gap SET gap_end=%s, state='open', reason=NULL, last_seen=now(), "
                "closed_at=NULL, first_seen=now(), pull_count=0, hit_rounds=%s, last_hit_round=%s "
                "WHERE sync_id=%s AND symbol=%s AND gap_start=%s", reopen)
        if touch:
            cur.executemany("UPDATE sync_gap SET last_seen=now() "
                            "WHERE sync_id=%s AND symbol=%s AND gap_start=%s", touch)
        if pull:
            cur.executemany("UPDATE sync_gap SET pull_count = pull_count + 1 "
                            "WHERE sync_id=%s AND symbol=%s AND gap_start=%s", pull)
        conn.commit()
    return stats


def _sync_gap_sync_one(sync_id: str, symbol: str, *, win_lo: str, win_hi: str,
                       gaps: list[tuple[str, str]] | None = None,
                       uncertain_reason: str | None = None,
                       pulled_anchors: list[str] | None = None) -> dict:
    """**单 symbol** 便捷入口（per-date 族／单测用）——薄包装 scope 级 `_sync_gap_sync`。

    `round_id=None`（per-date 对账不做「连续 N 轮」判据——该判据是 per-symbol 旁路的门控）；
    返回体把 `new` 归一成旧形态 `[(gap_start, gap_end)]`（per-date 调用方只关心段）。
    """
    st = _sync_gap_sync(
        sync_id, symbols=[symbol], win_map={symbol: (win_lo, win_hi)}, round_id=None,
        gaps_map=None if gaps is None else {symbol: gaps},
        uncertain_map={symbol: uncertain_reason} if uncertain_reason else None,
        pulled_map={symbol: pulled_anchors} if pulled_anchors else None)
    st["new"] = [(a, e) for _s, a, e in st["new"]]
    return st


def _alert_sync_gaps(label: str, spans: list[str]) -> None:
    """缺口**聚合**告警：整轮至多 1 条，只对**本轮新 open** 响铃（防首跑全史告警风暴）。

    新 code **`sync.gap`**（仓规：新 alert code → `alert_notify/runbook.py` RUNBOOK）。
    与 `_alert_sync_failure` 同范式：warn 级站内铃铛（notify 同标题 1min 去重），异常不阻断同步。
    """
    if not spans:
        return
    try:
        from src.alert_notify.notify import notify
        notify("warn", "data", f"数据缺口 {label}",
               f"本轮新增 {len(spans)} 段：{_gap_span(spans)}。"
               f"per-date 已内联重拉，仍缺者下轮/周日对账续补；长期 open ＝ 存量缺口。",
               code="sync.gap")
    except Exception as e:
        logger.warning("缺口告警发送失败（不阻塞同步流程）: %s", e)


def _alert_generic(code: str, title: str, body: str) -> None:
    """对账侧告警的**统一出口**（warn 级站内铃铛；异常不阻断对账）。

    与 `_alert_sync_gaps` 同范式——**新 alert code 必须进 `alert_notify/runbook.py`**（仓规）。
    """
    try:
        from src.alert_notify.notify import notify
        notify("warn", "data", title, body, code=code)
    except Exception as e:
        logger.warning("告警发送失败（code=%s，不阻断对账）: %s", code, e)


def _alert_sm_universe_missing(sync_id: str, category: str, venue: str | None) -> None:
    """**V6**：SM **无**该族 symbol ⇒ 快照不完整（scope 级，per-symbol 级结构上不可达）。

    `_sm_universe` 就是遍历源 ⇒ 「SM 无 symbol」只可能整族为空。原实现只 `logger.warning`
    （静默度不足：日志无人看即等于没做）。升级为 alert code `sync.universe_missing`。
    ⚠️ **不引入外部对照宇宙**（交易所 instruments 列表）来探测——对账是**本地审计动作**，
    读外部 API 违「连源才需 → 接入层」且与 §九.1 依赖倒置冲突。
    """
    _alert_generic(
        "sync.universe_missing", f"对账标的宇宙缺失 {sync_id}",
        f"`security_master` 中 category={category}"
        + (f"/exchange={venue}" if venue else "")
        + " **零行** ⇒ 该族期望集为空、对账静默失效（列表同步未跑/未写 SM？）。")


def _alert_local_unreadable(sync_id: str, table: str) -> None:
    """**P1-4（步 4 盲审）**：`_local_dates_map` 整 scope 不可读 ⇒ 全族转 `uncertain`。

    与 V6（`:2315`）/V7（`:2347`）**同级**（都是 scope 级可见性），却唯此一路**无告警** ⇒
    bar 表系统性不可读（表未建/权限/连接）时，真实大段缺失被压成 `uncertain` 且**只**在
    journal 摘要里留 `uncertain=N` —— 无人盯日志即盲区。
    ⚠️ 本函数**不**主张缺口（`uncertain` 是 §5.4 的正确语义），只把「为什么变成不确定」喊出来。
    """
    _alert_generic(
        "sync.local_unreadable", f"对账本地不可读 {sync_id}",
        f"`{table}` 本地日期集**整 scope 读取失败** ⇒ 该 scope 全族转 `uncertain`（**不是**「无缺口」）；"
        f"bar 表系统性不可读时真实缺失会被压成不确定。查表是否存在/权限/连接，明细见 journal 摘要。")


def _alert_inception_cover_low(sync_id: str, covered: int, total: int, streak: int,
                               declared: bool | None = None) -> None:
    """**V7 / V5**：inception 真值可及性异常。

    - 覆盖率**长期**低（连续 ≥ `_SM_COVER_STALE_ROUNDS` 轮）⇒ 缺口被 §5.4 **永久静默抑制**
      （一次刷新网络抖动写 `None` 且未恢复即触发）——抑制本是安全网，失修即变盲区；
    - `declared` 非 `None` ⇒ **互证闸**（V5）判定：`_RECONCILE_UNKNOWN_INCEPTION` 的**声明**
      与「SM 有 symbol 且 `list_date` 空」的**实测**不符（快照新鲜时评估）。
    """
    if declared is None:
        body = (f"`{sync_id}` 覆盖率 {covered}/{total} **连续 {streak} 轮**不足 ⇒ "
                f"该 scope 的缺口被 uncertain 抑制且**判据不许过门**；查刷新链是否持续探不到源。")
    else:
        body = (f"`{sync_id}` 互证闸不符：声明 `unknown_inception`={declared}，"
                f"实测「有 symbol 且 list_date 空」={covered < total}（{covered}/{total}）⇒ "
                f"`_RECONCILE_UNKNOWN_INCEPTION` 与真实可及性**漂移**，须复核声明。")
    _alert_generic("sync.inception_cover_low", f"inception 可及性异常 {sync_id}", body)


def _alert_reconcile_budget(sync_id: str, segs: int, budget: int, kind: str) -> None:
    """**产出 D**：本轮该 scope 的**落表行数**超上界 ⇒ **不落主张**（`_GAP_ROW_BUDGET`）。

    **显名取舍：用漏报换稳定**——该 scope 当轮缺口**不报**，以**告警**替代**静默**；其判据
    **不许过门**。超阈通常预示 `floor`/期望集算错或库被污染，人工介入优于放任落表。
    """
    _alert_generic(
        "sync.reconcile_budget", f"对账体量超阈 {sync_id}",
        f"本轮落表行数 {segs} > 上界 {budget}（kind={kind}）⇒ **不落主张**（当轮漏报，"
        f"以告警替代静默）；查窗口下界/期望集是否异常，必要时调 `_GAP_ROW_BUDGET`。")


def _reconcile_dates(sync_id: str, *, table: str, date_expr: str, expected: list[str] | None,
                     win_lo: str, win_hi: str, where: str = "", where_params: tuple = (),
                     repull_fn: Callable[[str], None] | None = None,
                     symbol: str = "", max_repull: int | None = None) -> dict:
    """per-date：**日期级差集 → 内联重拉 → 视图式落表**（设计 §7.1 主路径；同步收尾步骤，非独立任务）。

    `expected` ＝ 本轮窗口内的期望日，**唯一来源** `_trade_dates_in_range`（覆盖感知）。
    `None` ⇒ 日历未覆盖 ⇒ **不确定**（落 `uncertain`，**不主张缺口、不重拉**）——这是步 2 双盲审
    P0-2 的落点：拉取侧可 fail-open 回 `freq="B"`，**对账侧必须 fail-closed 到 uncertain**
    （否则把节假日当期望日 ⇒ **假缺口**）。
    """
    if expected is None:
        _sync_gap_sync_one(sync_id, symbol, win_lo=win_lo, win_hi=win_hi,
                       uncertain_reason="calendar_uncovered")
        return {"expected": 0, "missing": 0, "repulled": 0, "repull_failed": 0,
                "still_missing": [], "gap_dates": [], "segments": [], "new_segments": [], "gaps": {},
                "uncertain": "calendar_uncovered"}
    if not expected:
        return {"expected": 0, "missing": 0, "repulled": 0, "repull_failed": 0,
                "still_missing": [], "gap_dates": [], "segments": [], "new_segments": [], "gaps": {},
                "uncertain": None}

    local = _local_dates(table, date_expr, where, where_params)
    if local is None:
        _sync_gap_sync_one(sync_id, symbol, win_lo=win_lo, win_hi=win_hi,
                       uncertain_reason="local_unreadable")
        return {"expected": len(expected), "missing": 0, "repulled": 0, "repull_failed": 0,
                "still_missing": [], "gap_dates": [], "segments": [], "new_segments": [], "gaps": {},
                "uncertain": "local_unreadable"}

    missing = [d for d in expected if d not in local]
    repulled = failed = 0
    if missing and repull_fn is not None:
        cap = len(missing) if max_repull is None else max(0, min(len(missing), max_repull))
        for d in missing[:cap]:
            try:
                repull_fn(d)
                repulled += 1
            except Exception as e:
                failed += 1
                logger.warning("对账内联重拉失败 %s %s: %s", sync_id, d, e)
        if cap < len(missing):
            logger.warning("对账内联重拉预算耗尽 %s：缺 %d 日、仅重拉 %d 日，余者落 open 下轮续补",
                           sync_id, len(missing), cap)
    still = missing
    if repulled:
        local2 = _local_dates(table, date_expr, where, where_params)
        if local2 is None:
            _sync_gap_sync_one(sync_id, symbol, win_lo=win_lo, win_hi=win_hi,
                           uncertain_reason="local_unreadable")
            return {"expected": len(expected), "missing": len(missing), "repulled": repulled,
                    "repull_failed": failed, "still_missing": [], "gap_dates": [], "segments": [],
                    "new_segments": [], "gaps": {}, "uncertain": "local_unreadable"}
        still = [d for d in missing if d not in local2]
    segs = _to_segments(still, expected)
    stats = _sync_gap_sync_one(sync_id, symbol, win_lo=win_lo, win_hi=win_hi, gaps=segs)
    return {"expected": len(expected), "missing": len(missing), "repulled": repulled,
            "repull_failed": failed, "still_missing": still, "gap_dates": still,
            "segments": segs, "new_segments": stats.get("new") or [], "gaps": stats,
            "uncertain": None}


def _list_repullable(sync_id: str, symbol: str) -> list[tuple[str, str]]:
    """限频重拉候选：`open` 行 `pull_count < _GAP_REPULL_MAX` 且距 `last_seen` ≥ 1 天。"""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT gap_start, gap_end FROM sync_gap WHERE sync_id=%s AND symbol=%s "
                "AND state='open' AND pull_count < %s "
                "AND last_seen <= now() - (%s * interval '1 day') ORDER BY gap_start",
                (sync_id, symbol, _GAP_REPULL_MAX, _GAP_REPULL_MIN_INTERVAL_DAYS))
            return [(r[0].isoformat(), r[1].isoformat()) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("限频重拉候选查询失败（%s/%s）: %s", sync_id, symbol, e)
        return []


def _repull_symbol(adapter, kind: str, sub_kind: str | None, freq: str,
                   src_sym: str, gap_start: str, gap_end: str) -> int:
    """按缺口区间重拉单标的（幂等 upsert）。返回写入行数（缺表/空帧 ⇒ 0）。"""
    from src.data_platform.db import save_bars
    from src.data_platform.rate_limit import rate_limit_context
    with rate_limit_context(_get_rate_ds(adapter.provider), "daily"):
        df = adapter.fetch_supply(
            kind, sub_kind, symbol=src_sym, freq=freq,
            start=_ymd(gap_start).strftime("%Y%m%d"), end=_ymd(gap_end).strftime("%Y%m%d"))
    if df is None or df.empty:
        return 0
    rows = adapter.to_bar_rows(df, freq)
    return save_bars(freq, rows) if rows else 0


def _sm_universe(category: str, venue: str | None = None) -> list[tuple[str, object, object]]:
    """per-symbol 对账的标的宇宙 —— **单一真源＝`security_master`**，
    返回 `[(vt_symbol, updated_at, list_date)]`。

    **禁按 symbol 前缀猜**（裁定 Q3，双同）：前缀法 `11/12/13` 实测**漏** `10`×13 只 + `14`×5 只
    共 18 只转债；`bar_1d` 里 stock/etf/convertible 同用 `.SHSE/.SZSE` 后缀 ⇒ 后缀也区分不了族。
    `venue` 非空（crypto）时按 `exchange` 收窄（`.BINANCE` / `.OKX` 共享 `bar_1d`）。

    `updated_at` 是**快照 age** 的度量（调用方据此判 `stale_source`，设计 §5.4）。

    **批 110**：一并取 `list_date`（inception 真值）——**不额外往返**，且供调用方
    ① 传 `_window_floors(inception=…)`（**消除 per-symbol N+1**）② 判「结构性未知」（`list_date` 空）
    ③ 算**覆盖率**（`sm_covered/sm_total`）。这是「一次读取满足多个消费者」的口。
    """
    sql = "SELECT vt_symbol, updated_at, list_date FROM security_master WHERE category=%s"
    params: tuple = (category,)
    if venue:
        sql += " AND exchange=%s"
        params = (category, venue)
    with get_conn() as conn:
        return list(conn.execute(sql, params).fetchall())


def _log_scope_summary(sync_id: str, stat: dict) -> None:
    """**产出 C**：每 scope 一行结构化摘要（唯一现成可观测通道＝`quant-journal`，事实 12）
    ⇒ 母设计 §八.5 的判据**可复核、可自动判定**（含 `round_id`/`N`/覆盖率/误报证据计数）。

    取数配方（RUNBOOK `sync.gap`）：
    `quant-journal -u quant-celery-worker@quant --since "1 week ago" | grep gap_reconcile`
    """
    logger.info(
        "gap_reconcile sync_id=%s round_id=%s symbols=%d uncertain=%d hit_ge_N=%d opened=%d "
        "closed=%d repulled=%d errors=%d false_=%d unknown_inception=%d sm_covered=%s/%s "
        "budget_exceeded=%d suppressed=%s stale=%d cover_low_streak=%s judged=%s N=%d",
        sync_id, stat.get("round_id"), stat.get("symbols", 0), stat.get("uncertain", 0),
        stat.get("hit_ge_n", 0), stat.get("new_open", 0), stat.get("closed", 0),
        stat.get("repulled", 0), stat.get("errors", 0), stat.get("false_", 0),
        stat.get("unknown_inception", 0), stat.get("sm_covered"), stat.get("sm_total"),
        stat.get("budget_exceeded", 0), int(bool(stat.get("suppressed"))),
        int(bool(stat.get("stale_source"))),
        stat.get("cover_low_streak"), stat.get("judged"), _GAP_JUDGE_ROUNDS)


def _reconcile_symbols() -> dict:
    """per-symbol 旁路对账（beat 周日 03:33）。**不进 `sync_config`**——对账无游标、非拉取任务
    （裁定 Q2；批 83b 的「收编进 sync_config」针对**拉取**任务，与本事无冲突）。

    批 110（**判据地基**）三条新增：
      ① **轮次身份**（`round_id`，Valkey 严格单调）＋ 行上 `hit_rounds/last_hit_round`
         ⇒「连续 N 轮同缺口」**可自动判定**（`_gap_judge`；含**断档复位**，见 `_sync_gap_sync`）；
      ② **inception 收编**（裁定 G①）⇒ 下界取口＝SM 单一真源 ＋ **覆盖率进摘要**
         ＋ **互证闸**（声明 vs 实测，**快照新鲜时**才评估）；
      ③ **读取规模契约**：按 scope **一次**取本地日期集（`_local_dates_map`）＋ **一次**取
         `sync_gap` 行（`_sync_gap_scope_rows`，重拉候选由它内存筛）⇒ 成本由 **scope 数**决定。

    每标的：下界＝`_window_floors(adapter, kind, cfg, sym=vt_symbol, inception=…)`（**复用批 108
    同一真源，不二算**，§5.3-1）；上界＝`end − publish_lag`。输出＝落表 ＋ 聚合告警 ＋ 限频重拉。

    §5.4 两道不确定门（**抑制一切缺口主张**）：`inception` 未知／SM 缺 `list_date`（裁定 F）
    ⇒ `inception_unknown`；SM 快照 age 超阈 ⇒ `stale_source`（§九.1 依赖倒置的闭合口）。
    ⚠️ per-symbol 重拉的 `except` **不吞 `SoftTimeLimitExceeded`**（批 110·D）：软时限直抛 ⇒
    **中止整轮**（**失去** per-symbol 隔离）；契约＝「整轮放弃、幂等、下轮续」。
    """
    out: dict[str, dict] = {}
    today_s = date.today().strftime("%Y%m%d")
    round_id = _next_round_id()      # **每轮入口无条件推进一次**（与 scope 是否写行无关）
    from datetime import datetime as _dtm, timezone as _tzz
    round_tot = {"scopes": 0, "symbols": 0, "uncertain": 0, "new_open": 0, "closed": 0,
                 "repulled": 0, "errors": 0, "hit_ge_n": 0, "false_": 0,
                 "unknown_inception": 0, "budget_exceeded": 0, "suppressed": 0}

    def _accumulate(s: dict) -> None:
        for k in ("symbols", "uncertain", "new_open", "closed", "repulled", "errors",
                  "hit_ge_n", "false_", "unknown_inception", "budget_exceeded", "suppressed"):
            round_tot[k] += s.get(k, 0)
        round_tot["scopes"] += 1

    for sync_id, spec in _RECONCILE_SYMBOL_SCOPE.items():
        kind, sub_kind, freq, table, tzname, category = spec
        cfg = _get_config(sync_id)
        if not cfg or not cfg.get("enabled"):
            out[sync_id] = {"skipped": "未配置/已禁用"}
            continue
        adapter = _get_supply_adapter(cfg)
        try:
            end_d = _crypto_end(today_s, adapter, kind)
        except Exception as e:
            out[sync_id] = {"error": f"窗口上界不可得: {type(e).__name__}: {e}"}
            continue
        end_s = end_d.strftime("%Y%m%d")
        venue = getattr(adapter, "venue", None)
        try:
            sm_rows = _sm_universe(category, venue)      # [(vt_symbol, updated_at, list_date)]
        except Exception as e:
            out[sync_id] = {"error": f"SM 读取失败: {type(e).__name__}: {e}"}
            continue
        if not sm_rows:
            # V6：「SM 无该族 symbol」＝**scope 级**快照不完整（per-symbol 级结构上不可达——遍历源
            # 就是 SM 本身）⇒ 由 warning 升级为 **alert code**，不静默跳过。
            logger.warning("per-symbol 对账：SM 无 %s%s 标的，跳过 %s（列表同步未跑？）",
                           category, f"/{venue}" if venue else "", sync_id)
            _alert_sm_universe_missing(sync_id, category, venue)
            out[sync_id] = {"skipped": "SM 无该族标的"}
            continue
        # 快照 age（§5.4 第一行）：SM 该族最新 `updated_at` 超阈 ⇒ 期望集不完整 ⇒ 全族 uncertain
        newest = max((r[1] for r in sm_rows if r[1]), default=None)
        stale = newest is None or (_dtm.now(_tzz.utc) - newest).days > _SNAPSHOT_STALE_DAYS
        symbols = [r[0] for r in sm_rows]
        sm_total = len(symbols)
        sm_covered = sum(1 for r in sm_rows if r[2])

        stat: dict = {"symbols": sm_total, "uncertain": 0, "new_open": 0, "closed": 0,
                      "repulled": 0, "errors": 0, "stale_source": stale,
                      "sm_covered": sm_covered, "sm_total": sm_total,
                      "unknown_inception": 0, "hit_ge_n": 0, "false_": 0,
                      "budget_exceeded": 0, "round_id": round_id,
                      "judged": round_id is not None, "suppressed": 0}

        # —— 互证闸（V5）：比对键＝「SM **有** symbol 且 `list_date` **空**」；只在**新鲜时**评估 ——
        if not stale:
            measured_empty = sm_covered < sm_total
            declared = sync_id in _RECONCILE_UNKNOWN_INCEPTION
            if measured_empty != declared:
                logger.warning("互证闸不符 %s：声明 unknown_inception=%s，实测 list_date 空=%s",
                               sync_id, declared, measured_empty)
                _alert_inception_cover_low(sync_id, sm_covered, sm_total, 0, declared=declared)

        # —— 覆盖率长期低（V7）：连续低轮次计数（Valkey，跨轮），每 N 轮响一次（防每轮重响）——
        # ⭐ 传 `round_id` ⇒ 断档复位（P1-5）：该 scope 整轮未评估时本函数不被调用 ⇒ 靠
        # `last_cover_round != round_id-1` 在**下次评估**归 1，否则会跨过断档假告警。
        streak = _bump_cover_low(sync_id, sm_covered < sm_total, round_id)
        stat["cover_low_streak"] = streak
        if (sm_covered < sm_total and streak is not None
                and streak >= _SM_COVER_STALE_ROUNDS
                and (streak == _SM_COVER_STALE_ROUNDS or streak % _SM_COVER_STALE_ROUNDS == 0)):
            _alert_inception_cover_low(sync_id, sm_covered, sm_total, streak)

        news: list[str] = []
        if stale:
            _sync_gap_sync(sync_id, symbols=symbols,
                           win_map={s: (end_s, end_s) for s in symbols},
                           round_id=round_id,
                           uncertain_map={s: "stale_source" for s in symbols})
            stat["uncertain"] = sm_total
        else:
            dexpr = f"to_char(ts AT TIME ZONE '{tzname}','YYYYMMDD')"
            # ① 一次取本地日期集（读侧批量）② 一次取 sync_gap 行（重拉候选内存筛，不逐标的查）
            local_map = _local_dates_map(table, dexpr, symbols)
            if local_map is None:
                # P1-4（步 4 盲审）：`_local_dates_map` 返回 `None` ⇒ **整 scope** 不可读 ⇒
                # 下面逐标的落 `uncertain(local_unreadable)` ⇒ 真实大段缺失被**压成 uncertain**，
                # 而仅 journal 摘要里有 `uncertain=N`（无人盯日志＝盲区）。与 V6/V7 同级问题
                # （都是 scope 级可见性）⇒ 同制响亮告警。
                _alert_local_unreadable(sync_id, table)
            prev_map = _sync_gap_scope_rows(sync_id, symbols)

            win_map: dict[str, tuple[str, str]] = {}
            uncertain_map: dict[str, str] = {}
            gaps_map: dict[str, list | None] = {}
            exp_map: dict[str, list[str]] = {}
            for vt_symbol, _upd, ld in sm_rows:
                # 下界＝三方地板（批 108 同一真源，不二算）；`inception` 由**批量预取**传入（消 N+1）
                floor, _excl = _window_floors(adapter, kind, cfg, sym=vt_symbol, inception=ld)
                win_map[vt_symbol] = ((floor or end_d).strftime("%Y%m%d"), end_s)
                if not ld:
                    # 裁定 F / §5.4：真上币日不可得（结构性未知）⇒ **不主张任何缺口**
                    uncertain_map[vt_symbol] = "inception_unknown"
                    stat["unknown_inception"] += 1
                    continue
                if floor is None or floor > end_d:
                    gaps_map[vt_symbol] = None   # 下界晚于窗口上界（新上币）⇒ 无作业、非缺口
                    continue
                if local_map is None:
                    uncertain_map[vt_symbol] = "local_unreadable"
                    continue
                lo_s = floor.strftime("%Y%m%d")
                win_map[vt_symbol] = (lo_s, end_s)
                if tzname == "UTC":
                    expected = [(floor + timedelta(days=i)).strftime("%Y%m%d")
                                for i in range((end_d - floor).days + 1)]
                else:
                    expected = _trade_dates_in_range(lo_s, end_s)
                    if expected is None:
                        uncertain_map[vt_symbol] = "calendar_uncovered"
                        continue
                exp_map[vt_symbol] = expected
                local = local_map.get(vt_symbol, set())
                gaps_map[vt_symbol] = _to_segments([d for d in expected if d not in local], expected)

            # —— 体量上界（产出 D）：**先判预算再动作**（超阈 ⇒ 不重拉、不主张；告警替代静默）——
            total_segs = sum(len(v) for v in gaps_map.values() if v)
            budget = _GAP_ROW_BUDGET.get(kind, _GAP_ROW_BUDGET["bar_daily"])
            if total_segs > budget:
                stat["budget_exceeded"] = 1
                logger.error("对账体量超阈 %s：落表行数 %d > %d（kind=%s）⇒ 本轮不主张",
                             sync_id, total_segs, budget, kind)
                _alert_reconcile_budget(sync_id, total_segs, budget, kind)
                gaps_map = {s: None for s in gaps_map}      # 整 scope 静默不主张

            # —— 限频重拉（上轮遗留 open 行）→ 成功后由视图式同步自然闭合 ——
            repull_map = {} if stat["budget_exceeded"] else _repullable_map(prev_map)
            pulled_map: dict[str, list[str]] = {}
            for vt_symbol, cand in repull_map.items():
                src_sym = vt_symbol.rsplit(".", 1)[0]
                for g0, g1 in cand:
                    try:
                        _repull_symbol(adapter, kind, sub_kind, freq, src_sym, g0, g1)
                        stat["repulled"] += 1
                        pulled_map.setdefault(vt_symbol, []).append(g0)
                    except Exception as e:
                        if (_CelerySoftTimeLimit is not None
                                and isinstance(e, _CelerySoftTimeLimit)):
                            raise   # 软时限**直抛**（不吞）：中止整轮，下轮续（契约见 docstring）
                        stat["errors"] += 1
                        logger.warning("per-symbol 重拉失败 %s %s %s~%s: %s",
                                       sync_id, vt_symbol, g0, g1, e)
            if pulled_map:
                # 重拉后**一次**刷新（只对发生重拉的标的）⇒ 重算其段（不再逐标的查）
                refreshed = _local_dates_map(table, dexpr, list(pulled_map))
                if refreshed is not None:
                    local_map.update(refreshed)
                    for vt_symbol in pulled_map:
                        exp = exp_map.get(vt_symbol)
                        if exp is None or gaps_map.get(vt_symbol) is None:
                            continue
                        local = local_map.get(vt_symbol, set())
                        gaps_map[vt_symbol] = _to_segments(
                            [d for d in exp if d not in local], exp)

            # —— ③ 落表（**一次**批量写；`prev_map` 复用 ⇒ 不再读）——
            st = _sync_gap_sync(sync_id, symbols=symbols, win_map=win_map, round_id=round_id,
                                gaps_map=gaps_map, uncertain_map=uncertain_map,
                                pulled_map=pulled_map, prev_map=prev_map)
            stat["new_open"] = st["opened"] + st["reopened"]
            stat["closed"] = st["closed"]
            stat["uncertain"] = st["uncertain"]

            # —— 判据（纯函数；读**写后**的行——仍是常数级往返）——
            # 行形态转换：`_sync_gap_scope_rows` 给 7 元组；`_gap_judge` 要
            # `(symbol, gap_start, state, hit_rounds, last_hit_round)`（见其 docstring）。
            rows_after = [(s, r[0], r[2], r[5], r[6])
                          for s, rs in _sync_gap_scope_rows(sync_id, symbols).items()
                          for r in rs]
            # 门控**合取**（P1-1；方案 §产出B「budget_exceeded 的 scope 判据不许过门
            # （与覆盖率<1 同制）」）：本轮主张被抑制 ⇒ 不构成「命中」⇒ 判据不过门。
            _sup = bool(stat["budget_exceeded"]) or sm_covered < sm_total
            judge = _gap_judge(st, rows_after, round_id, suppressed=_sup)
            stat["hit_ge_n"] = judge["hit_ge_n"]
            stat["judged"] = judge["judged"]
            stat["suppressed"] = judge["suppressed"]
            fe = judge["false_evidence"]
            stat["false_"] = len(fe["same_round_oscillation"]) + len(fe["suppress_leak"])
            news = [f"{sync_id}/{sym} {a}~{b}" for sym, a, b in st["new"]]

        out[sync_id] = stat
        _log_scope_summary(sync_id, stat)
        _accumulate(stat)
        if news:
            _alert_sync_gaps(sync_id, news)

    logger.info("gap_reconcile_done round_id=%s scopes=%d symbols=%d uncertain=%d hit_ge_N=%d "
                "opened=%d closed=%d repulled=%d errors=%d false_=%d unknown_inception=%d "
                "budget_exceeded=%d suppressed=%d N=%d",
                round_id, round_tot["scopes"], round_tot["symbols"], round_tot["uncertain"],
                round_tot["hit_ge_n"], round_tot["new_open"], round_tot["closed"],
                round_tot["repulled"], round_tot["errors"], round_tot["false_"],
                round_tot["unknown_inception"], round_tot["budget_exceeded"],
                round_tot["suppressed"], _GAP_JUDGE_ROUNDS)
    return out


# ── 批 103b：聚宽 A 股日线（窗口动态取 + 行预算 + 按交易日分片） ──

_JQ_ROW_RESERVE = 50_000   # 额度安全余量（100 万条留 5%，避免贴线触发上游拒绝）


def _sync_astock_daily_jq(cfg: dict, end_date: str, backfill_from: str | None = None,
                          progress_cb: Callable | None = None) -> dict:
    """聚宽 JQData A 股日线 → `bar_1d`（同键 upsert；**只互写 OHLCV**）。

    三条硬约束（2026-10-06 实测，见 `flow/任务/批103b-聚宽真接.md` §0）：

    1. **窗口动态取**：`get_account_info()` 的 `date_range_start/end` 是**绝对区间**
       （实测 2025-06-28~2026-07-05），非「今日−15月~今日−3月」滚动式 ⇒ **禁硬编码**
       （续期/到期会变）。
    2. **自夹 start**：jqdatasdk 的边界检查**只查 `end_date`、不查 `start_date`**
       （实测 `2020-01-01~2026-01-01` 放行返 1455 行）⇒ 不夹 start 就是**额度炸弹**
       （单次请求可静默拉回全史，并把 config 的 `retention` 变成谎言）。
    3. **行预算 + 交易日分片**：额度按**返回行数**计（100 万/日）⇒ 按交易日推进、累计近预算即止。
       `cursor_upto` = **已完成的最后交易日**（`sync()` 用它推游标 ＝ `last_sync_date`，
       下轮 `+1 天` 续传）——**永不跳日**；一天都没完成时退回 `start-1`，下轮重试该日。

    **复权因子不提供**（见 §0.4）：adapter 的 `to_bar_rows` 写 `None`，由既有
    `backfill_adj_factor`（tushare 独有通道）补齐——本 handler 不碰因子。

    **provider 级互斥**（试用账号连接数=1）：`SyncLock("provider:joinquant")`——per-sync_id
    锁不够（两个 joinquant 同步项同刻 auth 会撞连接）。
    """
    from src.data_platform.db import save_bars_overwrite
    from .sync_lock import SyncLock

    def _d(s: str) -> date:
        return date(int(str(s)[:4]), int(str(s)[4:6]), int(str(s)[6:8]))

    adapter = _get_kline_adapter(cfg)
    prev_cursor = str(cfg.get("last_sync_date") or "")

    # 先取窗口（auth 属元数据调用，不耗行额度）——凭证缺失在此**响亮**抛 ProviderConfigError
    win = adapter.account_window()
    end_d = min(_d(end_date), win["end"])
    if backfill_from:
        start_d = _d(backfill_from)
    else:
        start_d = (_d(prev_cursor) + timedelta(days=1)) if prev_cursor else win["start"]
    # 批 108·步 3：起点自夹（SDK 不查 start）＋ **家族级三方地板**。聚宽族无 per-symbol
    # 生命周期 ⇒ 只能靠 `retention`（＝任务列；本族未声明）＋ `available_range`（＝账号窗口，
    # 步 1 实现）——设计 §九.7「无 inception 族只能靠 retention（＋源下界）」。
    _floor, _excl = _family_start(adapter, "bar_daily", cfg)
    start_d = max(start_d, win["start"], _floor)
    start_s, end_s = start_d.strftime("%Y%m%d"), end_d.strftime("%Y%m%d")

    if start_d > end_d:
        # 窗口内已全部完成（或本次请求与窗口无交集）——不推进（cursor=窗口止，稳定不 churn）
        return {"pulled": 0, "saved": 0, "start": start_s, "failed_dates": [],
                "expected_days": 0, "actual_days": 0, "log_status": "skipped",
                "cursor_upto": end_s, "excluded": _excl}

    with SyncLock("provider:" + str(adapter.provider)) as plock:
        if not plock.acquired:
            # 连接数=1：另一个聚宽任务在跑。**不推进游标**（保守回退到本次起点前一日）
            return {"pulled": 0, "saved": 0, "start": start_s, "failed_dates": [],
                    "expected_days": None, "actual_days": 0, "log_status": "skipped",
                    "cursor_upto": (start_d - timedelta(days=1)).strftime("%Y%m%d"),
                    "excluded": _excl}

        budget = max(0, int(win["spare"]) - _JQ_ROW_RESERVE)
        if budget <= 0:
            # 当日额度已耗尽（同日内多轮/被别处占用）——不推进，次日续
            return {"pulled": 0, "saved": 0, "start": start_s, "failed_dates": [],
                    "expected_days": None, "actual_days": 0, "log_status": "skipped",
                    "cursor_upto": (start_d - timedelta(days=1)).strftime("%Y%m%d"),
                    "excluded": _excl}

        symbols = _list_static_ts_codes("astock")
        if not symbols:
            # 在册标的为空＝上游静态表未同步——响亮失败（静默 0 行会被记 success 并推游标）
            raise RuntimeError("聚宽日线：在册 A 股标的为空（asset_static_info / static_symbols 未同步？）")

        _trade_days = _trade_dates_in_range(start_s, end_s)
        if _trade_days is not None:
            dates = _trade_days
        else:   # 日历未覆盖 → fail-open 逐自然日（宁多打不漏拉；**对账侧不跟随**，见下）
            dates = [(start_d + timedelta(days=i)).strftime("%Y%m%d")
                     for i in range((end_d - start_d).days + 1)]

        def _repull_day(day: str) -> None:
            """单日重拉（对账内联重拉用）——与主循环同一条 adapter 路径，`overwrite` 同语义。"""
            df = adapter.pull_daily_batch(day, "astock", symbols=symbols)
            if df is not None and not df.empty:
                rows = adapter.to_bar_rows(df, "1D")
                if rows:
                    save_bars_overwrite("1D", rows)

        pulled = saved = 0
        failed: list[str] = []
        reached = ""
        skipped: list[str] = []
        for i, d in enumerate(dates, 1):
            if pulled >= budget:
                break
            try:
                df = adapter.pull_daily_batch(d, "astock", symbols=symbols)
                skipped.extend(getattr(adapter, "last_skipped", None) or [])
                if df is not None and not df.empty:
                    rows = adapter.to_bar_rows(df, "1D")
                    if rows:
                        pulled += len(rows)
                        saved += save_bars_overwrite("1D", rows)
                reached = d
            except Exception as e:
                failed.append(f"{d}:{type(e).__name__}:{str(e)[:60]}")
            if progress_cb:
                progress_cb(i, len(dates), d)

        cursor = reached or (start_d - timedelta(days=1)).strftime("%Y%m%d")
        if pulled == 0 and not failed:
            failed.append("no_rows:窗口内 0 行（上游不可达 / 窗口压空 / 非交易日）")
        # 批 109：日期级差集（只在**行额度余量**内重拉；余者落 open 下轮续补）
        _rec = _reconcile_dates(
            cfg["id"], table="bar_1d",
            date_expr="to_char(ts AT TIME ZONE 'Asia/Shanghai','YYYYMMDD')",
            where="symbol IN (SELECT vt_symbol FROM security_master WHERE category='stock')",
            expected=_trade_days, win_lo=start_s, win_hi=end_s,
            repull_fn=_repull_day, max_repull=max(0, budget - pulled))
        logger.info("astock_daily_jq %s~%s：%d 交易日，拉 %d 行，存 %d 行，失败 %d，"
                    "跳过 %d（非 XSHE/XSHG，如 %s）（额度余 %d/%d）",
                    start_s, end_s, len(dates), pulled, saved, len(failed),
                    len(set(skipped)), sorted(set(skipped))[:3], win["spare"], win["total"])
        return {"pulled": pulled, "saved": saved, "start": start_s,
                "failed_dates": failed, "expected_days": len(dates),
                "actual_days": len([d for d in dates if d <= cursor]) if cursor else 0,
                "cursor_upto": cursor,
                "excluded": _excl, "gap_dates": _rec["gap_dates"], "reconcile": _rec}


_HANDLERS = {
    "astock_basic": _sync_astock_basic,
    "astock_list": _sync_astock_list,
    "cb_basic": _sync_cb_basic,
    "etf_list": _sync_etf_list,
    "trade_cal": _sync_trade_cal,
    # 批 83b：两条原独立 beat 收编（tasks.py 的 @app.task 逻辑迁入本表；
    # app.py beat 条目退役，改由 sync_config 行 + data_sync_scheduler 调度）。
    # sync_id 取**目标表名**（static_symbols / convertible_terms）——避免与
    # sync_kind_config 的 kind 词（static_list）混读。
    "static_symbols": _sync_static_list,
    "convertible_terms": _sync_convertible_terms,
    # 批 83b：池内深度数据两条 beat 收编（原独立 celery beat：5 分钟增量轮 + 周日全量校准）。
    # 拉取已下沉 adapter.fetch（POOL_TABLE_SPECS）；本处只挂编排 handler。
    "pool_data": _make_pool_handler(full=False),
    "pool_data_full_calibrate": _make_pool_handler(full=True),
    # 批 101：加密数据层第一步（原 beat `data-increment-crypto` 收编——原实现是
    # 「每 15min 被唤醒、永远 return skipped」的死构件，现真落地为币安永续 T+1 日线）
    "binance_perp_daily": _sync_binance_perp_daily,
    # 批 101b：同族三条（小时/1min/15min）——同一工厂产出，仅 (kind, freq, 首跑窗口) 不同；
    # 落表由 `bar_{freq.lower()}` 决定（bar_1h / bar_1min / bar_15min）
    "binance_perp_hourly": _sync_binance_perp_hourly,
    "binance_perp_1min": _sync_binance_perp_1min,
    "binance_perp_15min": _sync_binance_perp_15min,
    # 批 103b：聚宽 A 股历史切片（独立 sync_id——窗口无最近 3 个月，不占 astock_daily 切换位）
    "astock_daily_jq": _sync_astock_daily_jq,
    # 批 102b：OKX 永续日线（第 2 个 crypto 数据源——验证「多加密市场共存」；
    # 与 binance 同一条 `_make_crypto_bar_handler` 链，仅 adapter/label 不同）
    "okx_perp_daily": _sync_okx_perp_daily,
}

# 批 72（H12 一步切）：bar 族 6 键静态路由 _sync_via_kind——与 _HANDLERS 互斥=单源路由
# （防双注册钉=本文件 tier1 注册循环后的 assert，钉全量 14 键）
_VIA_KIND_IDS = frozenset({
    "astock_daily", "etf_daily", "cb_daily", "index_daily",
    "astock_minute", "astock_minute_5min",
})


def _read_sync_kind(sync_id: str) -> dict:
    """读 sync_kind_config 归置行（kind/sub_kind/pg_table/rebuild/pk_cols）。无行/表缺返回 {}。

    pk_cols（批 83b 补）：池内族通用落库的 ON CONFLICT 主键来源——落库层不硬编码表名/主键。
    """
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT kind, sub_kind, pg_table, rebuild, pk_cols FROM sync_kind_config "
                "WHERE sync_id=%s",
                (sync_id,))
            row = cur.fetchone()
    except psycopg.errors.UndefinedTable:
        return {}
    if not row:
        return {}
    return {"kind": row[0], "sub_kind": row[1], "pg_table": row[2], "rebuild": row[3],
            "pk_cols": list(row[4] or [])}


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
    # 批 99：同 _sync_by_trade_date 接交易日历（bar 族日线回补也走本函数，原同为 freq="B"）
    _trade_days = _trade_dates_in_range(start, end_date)
    if _trade_days is not None:
        date_range = _trade_days
    else:
        date_range = [d.strftime("%Y%m%d")
                      for d in pd.date_range(start=start, end=end_date, freq="B")]
    total = len(date_range)
    total_pulled = 0
    total_saved = 0
    failed_dates: list[str] = []
    last_success_date: str | None = None
    broken = False

    def _pull_day(day: str) -> tuple[int, int]:
        """单日拉取＋落库——**主循环与对账内联重拉共用同一实现**（防两套语义漂移）。"""
        with rate_limit_context(ds, api_name):
            frame = _fetch_supply(adapter, kind="bar_daily", sub_kind=sub,
                                  symbols=(), start=day, end=day, freq="1D")
        if not frame.rows:
            return 0, 0
        return len(frame.rows), _save_bars(list(frame.rows))

    for i, d in enumerate(date_range, 1):
        trade_date = d if isinstance(d, str) else d.strftime("%Y%m%d")
        try:
            _p, _s = _pull_day(trade_date)
            total_pulled += _p
            total_saved += _s
        except Exception as e:
            failed_dates.append(f"{trade_date}:{type(e).__name__}:{str(e)[:40]}")
            broken = True
        else:
            if not broken:
                last_success_date = trade_date
        if progress_cb:
            progress_cb(i, total, trade_date)

    # 批 109（设计 §7.1 主路径）：日期级差集 → 内联重拉 → 仍缺落 sync_gap ＋ 聚合告警。
    # 期望集**唯一来源**＝上面的 `_trade_days`（覆盖感知）；`None` ⇒ 对账侧 **fail-closed 到
    # uncertain**（拉取侧上面已 fail-open 回 `freq="B"`——两者刻意不同，见 `_reconcile_dates`）。
    # 「缺≠失败」：主循环把「上游返回空帧」记成成功，差集才能抓到这类**静默洞**。
    _rec = _reconcile_dates(
        cfg["id"], table="bar_1d",
        date_expr="to_char(ts AT TIME ZONE 'Asia/Shanghai','YYYYMMDD')",
        where="symbol IN (SELECT vt_symbol FROM security_master WHERE category=%s)",
        where_params=(sub,), expected=_trade_days, win_lo=start, win_hi=end_date,
        repull_fn=_pull_day)

    return {
        "pulled": total_pulled,
        "saved": total_saved,
        "failed_dates": failed_dates,
        "expected_days": _expected_trading_days(start, end_date),
        "actual_days": len(date_range) - len(failed_dates),
        "last_success_date": last_success_date,
        "start": start,
        "gap_dates": _rec["gap_dates"],
        "reconcile": _rec,
    }


def _sync_via_kind_cb_daily(adapter, *, sync_id: str, start: str, end_date: str,
                            progress_cb: Callable | None = None) -> dict:
    """bar_daily+convertible 区间（镜像已退役的 _sync_cb_daily——批 72 一步切）：单次 fetch → _save_bars；无 last_success_date。

    批 109：**主拉取是整段单 fetch**（不逐日）⇒ 缺日无「逐日循环」可依赖，对账的内联重拉走
    **单日区间** `_fetch_supply(kind='bar_daily', sub_kind='convertible', (day,day))`（任务文件产出 2
    明写）。转债宇宙取 SM `category='convertible'`（裁定 Q3：前缀法 `11/12/13` 实测漏 `10`×13+`14`×5）。
    """
    from src.data_platform.rate_limit import rate_limit_context

    def _pull_day(day: str) -> tuple[int, int]:
        with rate_limit_context(_get_rate_ds(adapter.provider), "cb_daily"):
            frame = _fetch_supply(adapter, kind="bar_daily", sub_kind="convertible",
                                  symbols=(), start=day, end=day, freq="1D")
        if not frame.rows:
            return 0, 0
        return len(frame.rows), _save_bars(list(frame.rows))

    with rate_limit_context(_get_rate_ds(adapter.provider), "cb_daily"):
        frame = _fetch_supply(adapter, kind="bar_daily", sub_kind="convertible",
                              symbols=(), start=start, end=end_date, freq="1D")
    pulled = len(frame.rows)
    saved = _save_bars(list(frame.rows)) if frame.rows else 0

    # 批 109：整段单 fetch 的族**尤其**需要日期级差集——空帧/部分缺都不进 failed_dates，
    # 只有差集能把「拉回了但某几天没有」暴露出来。
    _rec = _reconcile_dates(
        sync_id, table="bar_1d",
        date_expr="to_char(ts AT TIME ZONE 'Asia/Shanghai','YYYYMMDD')",
        where="symbol IN (SELECT vt_symbol FROM security_master WHERE category='convertible')",
        expected=_trade_dates_in_range(start, end_date), win_lo=start, win_hi=end_date,
        repull_fn=_pull_day)
    return {"pulled": pulled, "saved": saved, "start": start,
            "failed_dates": [], "expected_days": _rec["expected"],
            "actual_days": _rec["expected"] - len(_rec["gap_dates"]),
            "gap_dates": _rec["gap_dates"], "reconcile": _rec}


def _sync_via_kind_index(adapter, *, start: str, end_date: str,
                         progress_cb: Callable | None = None) -> dict:
    """index_daily 区间（镜像已退役的 _sync_index_daily〔000300.SH〕——批 72 一步切）：单次 fetch → save_index_bars；无 last_success_date。"""
    from src.data_platform.db import save_index_bars
    from src.data_platform.rate_limit import rate_limit_context
    with rate_limit_context(_get_rate_ds(adapter.provider), "index_daily"):
        frame = _fetch_supply(adapter, kind="index_daily", sub_kind=None, symbols=("000300.SH",),
                              start=start, end=end_date, freq="1D")
    saved = save_index_bars(list(frame.rows)) if frame.rows else 0
    return {"pulled": len(frame.rows), "saved": saved, "start": start,
            "failed_dates": [] if frame.rows else ["index_daily:empty"],
            "expected_days": None, "actual_days": None}


def _sync_via_kind_minute(adapter, *, sync_id: str, start: str, end_date: str,
                          progress_cb: Callable | None = None) -> dict:
    """bar_minute per-symbol（镜像已退役的 _sync_astock_minute——批 72 一步切）：逐只 fetch（分段在 adapter 内）→ save_bars(freq)。

    DB 写在 rate_limit_context 外（归因拆分，同已退役 _sync_astock_minute）；无 last_success_date。
    """
    from src.data_platform.db import save_bars
    from src.data_platform.rate_limit import rate_limit_context
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
                frame = _fetch_supply(adapter, kind="bar_minute", sub_kind=None, symbols=(tc,),
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
    非 bar 族 kind 抛 UnsupportedFeature 兜底（批 72：bar 族 6 键静态路由 _VIA_KIND_IDS）。
    """
    from src.data_platform.adapters.base import UnsupportedFeature
    sync_id = cfg["id"]
    row = _read_sync_kind(sync_id)
    if not row:
        # 必须 raise（不能 return error status）——sync() 不查 r.get("status")，返回 error 会被当
        # 成功并无条件推进游标跳过整个窗口（数据丢失）。raise 让 sync() 外层 except 收编→error+不动游标。
        raise RuntimeError(f"sync_kind_config 无归置行: {sync_id}（迁移 0094 未上产）")
    kind = row["kind"]
    sub = row["sub_kind"]
    # index_daily 走已退役 sync_benchmark_index 同款 adapter 选择（_get_kline_adapter({})——指数非 K 线
    # 数据面，绕开 M2 试点 resolve 选源，否则 routing_kline_pilot=on 时会误路由到缺能力源）
    adapter = _get_kline_adapter({} if kind == "index_daily" else cfg)

    # 批 108·步 3：下界＝三方地板取 max（`retention` 真读 ＋ 源界）——替换三处**假首跑地板**：
    # ① `index_daily` 的硬编码 `20050408`（现由 `retention=2005-04-08` 承载，值不变）
    # ② `default_days`（7 / 30）③ 无游标分支的「今日回推」。
    # ⚠️ 本族**无 inception**（per-date 拉取，无逐个标的生命周期）⇒ 只能靠 retention ＋ 源界（§九.7）。
    floor_s, _fam_excl = _family_start(adapter, kind, cfg)
    floor_s = floor_s.strftime("%Y%m%d")
    if backfill_from:
        start = max(backfill_from, floor_s)          # 回补显式起点仍受地板夹（源拿不到更早）
    else:
        last = cfg.get("last_sync_date")
        start = ((pd.Timestamp(last) + timedelta(days=1)).strftime("%Y%m%d")
                 if last else floor_s)
        if start > end_date:
            return {"pulled": 0, "saved": 0, "start": start, "failed_dates": [],
                    "expected_days": 0, "actual_days": 0, "excluded": _fam_excl}

    if kind == "bar_daily" and sub in ("stock", "etf"):
        r = _sync_via_kind_daily_batch(adapter, sub=sub, cfg=cfg, start=start,
                                       end_date=end_date, progress_cb=progress_cb)
    elif kind == "bar_daily" and sub == "convertible":
        r = _sync_via_kind_cb_daily(adapter, sync_id=sync_id, start=start,
                                    end_date=end_date, progress_cb=progress_cb)
    elif kind == "index_daily":
        r = _sync_via_kind_index(adapter, start=start, end_date=end_date, progress_cb=progress_cb)
    elif kind == "bar_minute":
        r = _sync_via_kind_minute(adapter, sync_id=sync_id, start=start, end_date=end_date,
                                  progress_cb=progress_cb)
    else:
        raise UnsupportedFeature(f"通用引擎未实现 kind={kind}, sub_kind={sub}（bar 族外后续切）")
    # 批 109（P1 接线面补全）：批 108 的 `excluded` 原先**只有 crypto 工厂产出**，per-date 族
    # 把 `_family_start` 的第二返回值丢了 ⇒ `policy_discard`/`unreachable` 静默。本处透传，
    # 由 `sync()` 落 `sync_gap` **终态**行（幂等）。
    if _fam_excl:
        r["excluded"] = _fam_excl
    return r


# ═══ 三档数据第一档：全局定时同步 handler（U 审 2026-08-19）═══
# 通用模式：pull(trade_date) → DataFrame → 逐行 upsert 到专用表
# soft_time_limit 由 celery task 侧覆盖（≥600s），此处只做数据层

# 批 92：窗口下界回看重叠（自然日）。上游某日数据迟发布（>T+1）时，游标已推进到可用日
# ⇒ 下一轮窗口不再含该日 ⇒ 永久漏。回看 K 天用 upsert 幂等重拉，把这类延迟兜进窗口。
_TIER1_OVERLAP_DAYS = 3

# 批 92：按 sync_id 声明的「数据可用延迟」（交易日数）——窗口上界＝today 回推 N 个交易日。
# 未声明者 lag=0（盘后当日可得）。T+1 表必须显式声明，否则前沿日恒空拉。
_TIER1_LAG_TRADING_DAYS: dict[str, int] = {"margin_detail_sync": 1}


def _make_tier1_handler(kind: str, sub_kind: str | None, table: str, pk_cols: list[str],
                        float_cols: list[str] | None = None, text_cols: list[str] | None = None,
                        lag_trade_days: int = 0, date_param: str = "trade_date",
                        sync_id: str | None = None):
    """工厂：生成第一档按日批量同步 handler（批 100：拉取改走 `adapter.fetch_supply(kind, sub_kind)`）。

    Args:
        kind / sub_kind: 归置键（真源 `sync_kind_config`；批 100 前本工厂硬编码 `importlib` 直连
            tushare_adapter ⇒ `sync_config.provider` 对这些项失效。现由 provider 选 adapter。
        table: 目标表名（如 'stk_limit'）
        pk_cols: 主键列（用于 ON CONFLICT）
        float_cols: NUMERIC 列名列表
        text_cols: TEXT 列名列表
        lag_trade_days: 数据可用延迟（交易日数，批 92）——窗口上界＝today 回推 N 个交易日。
            0＝当日盘后可得（默认）；1＝T+1（如 margin_detail，09:00 拉昨日）。
        date_param: 源侧窗口参数名——`trade_date`（常规，默认）/ `ann_date`（公告日驱动，如 forecast）。
        sync_id: 注册处传入的同步项 id（批 109 日期级对账的 `sync_gap` scope 键）。缺省时回落
            `cfg['id']`（运行期配置恒有），再回落 `kind`（仅供直接构造 handler 的单元测试）。
    """
    from src.data_platform.adapters.tushare_adapter import _safe_float   # 值归一（非分派，源无关）
    all_cols = (float_cols or []) + (text_cols or [])
    conflict = ", ".join(pk_cols)
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in all_cols if c not in pk_cols)

    def _handler(cfg: dict, end_date: str, backfill_from: str | None = None,
                 progress_cb=None) -> dict:
        """通用第一档同步：按 trade_date 拉全市场 → upsert。"""
        from src.data_platform.db import get_conn as _gc
        from src.data_platform.rate_limit import rate_limit_context
        prov = _provider_of(cfg)
        adapter = _get_supply_adapter(cfg)
        # 批 83b：限速/熔断/用量归属随 sync_config.provider 走（原死钉 get_data_source("tushare")）
        ds = _get_rate_ds(prov)
        # 批 92：窗口上界＝数据可用日（不再无条件用 sync() 传入的 today）——T+1 表
        # （margin_detail，lag=1）上界＝上一交易日，逐日循环不再空拉尚不可得的当日。
        end_date = _data_ready_end_date(lag_trade_days)
        # 修 2026-08-19：backfill_from 直接用（含当日）；增量才 +1 天（last_sync_date 的次日），
        # 批 92 再回看 _TIER1_OVERLAP_DAYS 天兜上游迟发布（upsert 幂等，不重复计数）。
        # 批 108·步 3：无游标时的下界＝三方地板（替换 `today−3d` 的**假首跑地板**）。
        # `_TIER1_OVERLAP_DAYS` 保留：它是**主动重叠**（兜上游迟发布），归批 109 的日期级对账
        # 取代（设计 §六），与下界无关。
        _floor_s, _fam_excl = _family_start(adapter, kind, cfg)
        _floor_s = _floor_s.strftime("%Y%m%d")
        if backfill_from:
            start_ts = max(backfill_from, _floor_s)
        else:
            _last = cfg.get("last_sync_date")
            start_ts = ((pd.Timestamp(_last)
                         + timedelta(days=1 - _TIER1_OVERLAP_DAYS)).strftime("%Y%m%d")
                        if _last else _floor_s)
        if start_ts > end_date:
            return {"pulled": 0, "saved": 0, "start": start_ts, "cursor_upto": end_date,
                    "failed_dates": [], "expected_days": 0, "actual_days": 0,
                    "excluded": _fam_excl}

        # 批 97：逐日循环接交易日历（法定节假日不再空调用）——日历未覆盖区间时
        # fail-open 回 freq="B"（宁多打、不漏拉）。trade days ⊆ business days，
        # 收窄只会少拉节假日，对 ann_date 驱动的表（forecast）也无回归。
        _trade_days = _trade_dates_in_range(start_ts, end_date)
        if _trade_days is not None:
            date_range = _trade_days
        else:
            date_range = [d.strftime("%Y%m%d")
                          for d in pd.date_range(start=start_ts, end=end_date, freq="B")]
        # 修 2026-08-19：insert_cols 含 PK，placeholders 必须同长（原漏 PK 导致每日 INSERT 失败）
        insert_cols = pk_cols + [c for c in all_cols if c not in pk_cols]
        placeholders = ", ".join(["%s"] * len(insert_cols))
        cols_sql = ", ".join(insert_cols)
        upsert = (f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders}) "
                  f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}" if updates else
                  f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders}) "
                  f"ON CONFLICT ({conflict}) DO NOTHING")

        def _pull_day(td: str) -> int:
            """单日拉取＋upsert——**主循环与对账内联重拉共用同一实现**。"""
            # forecast 按 ann_date 拉（公告日驱动），其余按 trade_date；
            # 限速（限流治理吸收）：原 0.3s 硬编码 → rate_limit_context 三级可调
            # 批 64b：档键从 "daily" 改 per-API（table==api 名，DEFAULT_RATE_LIMITS 各键 0.3s）
            with rate_limit_context(ds, table):
                # 批 100：拉取经 adapter（provider 生效）——日期参数由 date_param 声明
                df = adapter.fetch_supply(kind, sub_kind, **{date_param: td})
            if df is None or df.empty:
                return 0
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
                                vals.append(_safe_float(v) if v is not None else None)
                            else:
                                vals.append(str(v) if v is not None else None)
                        batch.append(tuple(vals))
                    cur.executemany(upsert, batch)
                conn.commit()
            # 批 117：st_list 落表后派生 security_state(st) 时变行（官方名单=当日快照，
            # 全市场两态全集——在档=is_st:true；不在档=非 ST（无行，effective_attr 查不到
            # 即非 ST）。fail-soft 同 _derive_st_states 范式。
            if table == "st_list":
                _derive_st_states_from_st_list(df)
            return len(df)

        total_pulled = total_saved = 0
        failed_dates = []
        for td in date_range:
            try:
                n = _pull_day(td)
                total_pulled += n
                total_saved += n
            except Exception as e:
                failed_dates.append(f"{td}:{type(e).__name__}:{str(e)[:40]}")
            if progress_cb:
                progress_cb(len(date_range), len(date_range), td)

        # 批 109（**§7.1 明写「TIER1 族当前 handler 自算窗口，收编时须一并纳入」**）：
        # 日期级差集。期望集与拉取循环**同一列表** `_trade_days`（覆盖感知）；`None` ⇒ uncertain。
        # 本地存在性比 `date_param` 对应的列（forecast 是 `ann_date`——拉取参数即落库列）。
        _rec = _reconcile_dates(
            sync_id or cfg.get("id") or kind, table=table, date_expr=date_param,
            expected=_trade_days, win_lo=start_ts, win_hi=end_date, repull_fn=_pull_day)
        return {"pulled": total_pulled, "saved": total_saved, "start": start_ts,
                "cursor_upto": end_date,
                "failed_dates": failed_dates,
                "expected_days": len(date_range), "actual_days": len(date_range) - len(failed_dates),
                "excluded": _fam_excl, "gap_dates": _rec["gap_dates"], "reconcile": _rec}

    return _handler


def _make_full_rebuild_handler(kind: str, sub_kind: str | None, table: str, pk_cols: list[str],
                              text_cols: list[str], date_param: str | None = None):
    """工厂：生成全量重建 handler（每周一跑，DELETE 全表后 INSERT）。

    批 100：拉取改走 `adapter.fetch_supply(kind, sub_kind)`（provider 生效）；`date_param=None`
    表示源接口无窗口参数（快照，如 namechange）——引擎对同族 kind 传统一形态，adapter 侧各取所需。

    批 56a·M1：重建后若表=namechange，追加派生 security_state(st) 时变行——
    ST 状态从曾用名推断（当前有效名含 ST→is_st=true，start_date=生效日，
    29 号 §四六源之一：st←namechange_sync，含 start_date 天然 PIT）。
    """
    def _handler(cfg: dict, end_date: str, backfill_from: str | None = None,
                 progress_cb=None) -> dict:
        from src.data_platform.db import get_conn as _gc
        from src.data_platform.rate_limit import rate_limit_context
        prov = _provider_of(cfg)
        adapter = _get_supply_adapter(cfg)
        _kw = {date_param: end_date} if date_param else {}
        with rate_limit_context(_get_rate_ds(prov), table):   # 批 64b 裸调收编（namechange/concept，档 0.3s）；批 83b provider 真路由；批 100 拉取经 adapter
            df = adapter.fetch_supply(kind, sub_kind, **_kw)
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
    ⚠️ 批 117 起 ST 判定真源=st_list 官方快照（perms 直读 st_list 表）；本派生保留为
    历史/降级参考（namechange 重建联动不删——任务书「namechange 派生保留为 fallback」）。
    """
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
                         {"name": name, "is_st": "ST" in name.upper()}))
        _sm_upsert_state(rows)
        logger.info("security_state(st) 派生 %d 行（namechange 重建）", len(rows))
    except Exception as e:
        logger.warning("security_state(st) 派生失败（不影响 namechange 同步）: %s", e, exc_info=True)


def _derive_st_states_from_st_list(df) -> None:
    """批 117：从 st_list 官方名单快照派生 security_state(st) 时变行（fail-soft）。

    与 namechange 派生的差异：官方名单是**当日全市场两态全集**——在档即 is_st=true
    （type_name 全「风险警示板」，步 0 实测），不在档=非 ST（无须写行——非 ST 是
    「无行」默认态）。effective_from=快照 trade_date（官方口径，非 today 兜底）。
    """
    try:
        rows = []
        for r in df.to_dict("records"):
            ts = r.get("ts_code")
            td = r.get("trade_date")
            name = r.get("name") if isinstance(r.get("name"), str) else ""
            if not isinstance(ts, str) or not ts:
                continue
            td = str(td) if td is not None else ""
            if len(td) == 8 and td.isdigit():
                eff = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
            else:
                continue
            rows.append((_ts_to_vt_prefix(ts), eff, "st",
                         {"name": name, "is_st": True, "type_name": r.get("type_name")}))
        _sm_upsert_state(rows)
        logger.info("security_state(st) 派生 %d 行（st_list 官方名单）", len(rows))
    except Exception as e:
        logger.warning("security_state(st) 派生失败（不影响 st_list 同步）: %s", e, exc_info=True)


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
    # 批 117：ST 官方名单快照（stock_st 实测列——type/type_name 全「风险警示板」）
    "st_list": ["name","type","type_name"],
}

# 按日批量的（7 个）。元组＝(kind, sub_kind, 目标表, 主键)。kind/sub_kind 真源＝sync_kind_config
# （批 100：test_batch100 与归置行对账，漂移即红）；date_param 声明源侧窗口参数名。
_TIER1_BATCH = {
    "stk_limit_sync":     ("stk_limit",      None,            "stk_limit",     ["trade_date","ts_code"]),
    "moneyflow_sync":     ("featured_daily", "moneyflow",     "moneyflow",     ["ts_code","trade_date"]),
    "margin_detail_sync": ("featured_daily", "margin_detail", "margin_detail", ["trade_date","ts_code"]),
    "top_list_sync":      ("featured_daily", "top_list",      "top_list",      ["trade_date","ts_code"]),
    "block_trade_sync":   ("featured_daily", "block_trade",   "block_trade",   ["ts_code","trade_date"]),
    "cyq_perf_sync":      ("featured_daily", "cyq_perf",      "cyq_perf",      ["ts_code","trade_date"]),
    "forecast_sync":      ("financial_stmt", "forecast",      "forecast",      ["ts_code","ann_date","end_date"]),
    # 批 117：ST 官方名单（按日全市场快照，范式照抄 stk_limit；ST 判定真源切此表）
    "st_list_sync":       ("featured_daily", "st_list",       "st_list",       ["trade_date","ts_code"]),
}
for _sid, (_kind, _sub, _tbl, _pk) in _TIER1_BATCH.items():
    _HANDLERS[_sid] = _make_tier1_handler(
        _kind, _sub, _tbl, _pk,
        float_cols=_TIER1_FLOAT_COLS.get(_tbl, []),
        text_cols=_TIER1_TEXT_COLS.get(_tbl, []),
        lag_trade_days=_TIER1_LAG_TRADING_DAYS.get(_sid, 0),
        date_param="ann_date" if _sid == "forecast_sync" else "trade_date",
        sync_id=_sid)

# 全量重建的（2 个）。元组＝(kind, sub_kind, 目标表, 主键, date_param)；date_param=None＝源无窗口（快照）
_TIER1_FULL = {
    "namechange_sync": ("static_list",    "namechange", "namechange", ["ts_code","name","start_date"], None),
    "concept_sync":    ("industry_class", "concept",    "concept",    ["ts_code"],                       "trade_date"),
}
for _sid, (_kind, _sub, _tbl, _pk, _dp) in _TIER1_FULL.items():
    _HANDLERS[_sid] = _make_full_rebuild_handler(
        _kind, _sub, _tbl, _pk, text_cols=_TIER1_TEXT_COLS.get(_tbl, []), date_param=_dp)

# 防双注册钉（批 72）：位置=两个 tier1 注册循环之后——钉全量 14 键（字面量 5+工厂 9），
# 未来往 _TIER1_BATCH/_TIER1_FULL 回填 bar 族键也在本钉覆盖内（双盲 A P1-2 修正——
# 原位于注册循环前只钉 5 键字面量，工厂回填路径静默失效）；测试钉=test_sync_via_kind 导入后态断言
assert not (_VIA_KIND_IDS & set(_HANDLERS))


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
    """查某标的**产生时间**（上市/上币日，YYYYMMDD）；不可得 ⇒ 回退 `_TUSHARE_MIN_DATE`。

    批 108·步 2（裁定 G①）：**真源改读 `security_master.list_date`**。原读
    `asset_static_info` / `etf_basic_info` / `cb_basic_info` 三张**源表**——与 SM 构成
    **inception 双源**（两处都由同一 df fan-out 写 ⇒ 可漂移），且那三表是 **tushare 专属**
    形状（§九.6「换源即错」）。SM 是**跨市场**主档 ⇒ 唯一真源，crypto 与 astock 同形取用。

    `kind` 参数保留（5 个调用点同签名），但本实现**不再按 kind 选表**——SM 按 `vt_symbol` 查。
    回退值 `_TUSHARE_MIN_DATE` 仍为 **tushare 专属**（挂账 G-3：真换源时须换）。
    """
    vt = _ts_to_vt_prefix(ts_code)
    with get_conn() as conn:
        cur = conn.execute("SELECT list_date FROM security_master WHERE vt_symbol=%s", (vt,))
        row = cur.fetchone()
    ld = row[0] if row else None
    if not ld or str(ld).strip() in ("", "None", "nan"):
        return _TUSHARE_MIN_DATE
    # 形如 1990-12-19 / 19901219
    s = str(ld).strip().replace("-", "").replace("/", "")
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
    #    批 83b：此处**故意**仍钉 tushare（不做 provider 真路由）——交易日历是交易所的
    #    市场级共同事实、非某数据源私有数据，P1-4 已裁"非 bar 项 provider 恒 tushare"；
    #    且本函数是无 cfg 的完整性工具（_find_gaps 调用），无 provider 可读。
    try:
        pro = _get_pro()
        start_y = int(start[:4])
        end_y = int(end[:4])
        all_d = []
        from src.data_platform.rate_limit import rate_limit_context
        _ds = _get_rate_ds("tushare")
        for y in range(start_y, end_y + 1):
            with rate_limit_context(_ds, "trade_cal"):   # 批 67：裸调收编（按年循环逐次包）
                df = pro.trade_cal(exchange="SSE", start_date=f"{y}0101", end_date=f"{y}1231")
                _ds.record_usage(api_calls=1, api_name="trade_cal", provider="tushare")
            if df is not None and not df.empty:
                all_d.extend([str(d) for d in df[df["is_open"] == 1]["cal_date"].tolist()])
        if all_d:
            all_d.sort()
            return all_d
    except Exception:  # 失败不阻断（fail-open 降级）  # noqa: S110
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
    原 _adj_map_for_df（批 72 已随旧 handler 退役）取首日因子应用到多日全区间，是「首日因子污染多日」的错值源。
    """
    from src.data_platform.rate_limit import rate_limit_context
    try:
        with rate_limit_context(_get_rate_ds(adapter.provider), "daily"):
            df = adapter.pull_daily(ts_code, start, end, kind=kind)
    except Exception:
        return None
    if df is None or df.empty:
        return df
    if "trade_date" not in df.columns:
        return None
    _log_bar_quality(df, _QUALITY_LABEL_OF.get(kind, kind))   # 批 72：per-symbol 收编（v2 #3）
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
    from src.data_platform.rate_limit import rate_limit_context
    with rate_limit_context(_get_rate_ds(adapter.provider), "daily"):
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
    from src.data_platform.rate_limit import rate_limit_context
    try:
        with rate_limit_context(_get_rate_ds(adapter.provider), "daily"):
            df = adapter.pull_daily(ts_code, start, end, kind=kind)
    except Exception as e:
        return {"status": "error", "error": f"{type(e).__name__}: {str(e)[:120]}"}

    if df is None or df.empty:
        return {"status": "empty", "pulled": 0, "saved": 0, "range": [start, end]}

    if "trade_date" not in df.columns:
        return {"status": "error", "error": f"响应缺 trade_date 列: {list(df.columns)[:4]}"}

    _log_bar_quality(df, _QUALITY_LABEL_OF.get(kind, kind))   # 批 72：backfill 收编（v2 #3 第 5 处——overwrite 写路径全仓唯一残口消除）

    # F-F2：单标的回补也带因子——to_bar_rows 不传 adj_map 时全 NULL，叠加 overwrite 的
    # DO UPDATE 会把已回填的 adj_factor 清回 NULL（E/F 盲审实测的数据破坏路径）；
    # 拉不到因子（降级）时 COALESCE 兜底不清空（schema.py BAR_TABLE_INSERT_OVERWRITE）
    try:
        with rate_limit_context(_get_rate_ds(adapter.provider), "adj_factor"):
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


def _delete_supply_item(sync_id: str, conn=None) -> dict:
    """批 107：供给面项的「切换重建」删除——**清整表** + 重置游标。

    与 `delete_by_sync_item` 的 per-symbol 分支并列：供给面项（`_LITERAL_SUPPLY` 7 项）的表是
    **全量重建/快照**语义（无 `symbol` 列、无 per-symbol 维度），per-symbol 分支不适用；
    「切换 provider 重建」的正确语义＝清空该表后由 handler 全量重拉。

    背景（批 83b P1-4 裁定）：非 bar 项要对前端**放开切换**，前提是 `delete_by_sync_item` 支持它
    ——否则 `switch_provider_api` 会 rollback + 500（「界面允许切、后端 500」）。本分支即该前提
    （判据门 `test_provider_switch_scope`）。

    ⚠️ 表名取 `_LITERAL_SUPPLY[sync_id][2]`（代码内字面量，非用户输入 ⇒ f-string 无注入面）；
    该值与 `sync_config.pg_table` / `sync_kind_config.pg_table` 三处一致，由
    `test_batch107::test_pg_table_single_source` + `test_literal_keys_match_placement_rows` 钉住
    （`etf_list` 原错配 `asset_static_info` 的清空会误删 A 股整表＝跨项数据丢失，已被修）。
    """
    entry = _LITERAL_SUPPLY.get(sync_id)
    if entry is None:
        return {"status": "error", "error": f"不支持删除: {sync_id}"}
    table = entry[2]

    def _body(c) -> int:
        try:
            cur = c.execute(f'DELETE FROM "{table}"')
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


def delete_by_sync_item(sync_id: str, conn=None) -> dict:
    """全量删该同步项的本地数据 + 重置游标（切换 provider 用，24 号 §2.3 切换重建原语）。

    删 bar 表里该 kind 的所有标的（批量 SQL），并重置 last_sync_date 游标——
    调用方随后 full 回填。静态表空（无法确定删什么）返回 error 防静默假删。
    批27-23：conn 可选传入——传入则事务归调用方（switch-provider 原子化：改 provider+删数据
    一次 commit，防"删成功改失败=数据已丢未切换"与倒置的"新配置+旧数据混合"两种中间态）。
    批 107：非 bar 供给面项走 `_delete_supply_item` 分支（清整表）——见其 docstring。
    """
    from src.data_platform.schema import to_vt_symbol
    meta = _PER_SYMBOL_META.get(sync_id)
    if meta is None:
        return _delete_supply_item(sync_id, conn)
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
                    _log_bar_quality(df, _QUALITY_LABEL_OF.get(kind, kind))   # 批 72：sync_all 收编（v2 #3）
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
    count_error: str | None = None
    try:
        # 批 91：只读全表聚合放宽 statement_timeout（prod 10s 下该查询被 PG 取消，
        # 此前只吞 UndefinedTable ⇒ 超时异常逃逸成 500，整页不可用）。
        with get_conn_for_heavy_read() as conn:
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
    except Exception as e:
        # 批 91：聚合失败不再逃逸成 500——降级为「清单可看、本地计数不可用」，并显著标出
        # （count_error 由前端提示，避免把「查不到」伪装成「本地 0 条」）。
        logger.warning("list_symbols(%s) 本地计数聚合失败（降级 local_count=0）: %s", sync_id, e)
        count_error = f"{type(e).__name__}: {str(e)[:160]}"

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
    out = {"items": items, "total": total}
    if count_error:
        # 批 91：本地计数聚合降级标记（前端须显著提示，勿把「查不到」显示成「0 条」）
        out["count_error"] = count_error
    return out


