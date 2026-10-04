"""池内深度数据同步（三档第二档，per-symbol）——**引擎编排层**（批 83b 收编后）。

本模块只保「通用编排」：资源锁防重叠、表级游标窗口策略、时间盒、逐标的错误聚合、
契约帧落库。**源特定**部分（源 API / 源参数 / 窗口参数形态 / 值归一 / 列形状声明）已
下沉 `data_platform.adapters.tushare_adapter.POOL_TABLE_SPECS` + `TushareAdapter.fetch`，
拉取统一走 `adapter.fetch(DataRequest)` 契约（任务书 83b「分层判据」：不同的下沉插件、
通用的保留上层；多源 = 新增 adapter 实现同族 kind 的 fetch + 注册，本模块零改动）。

调用面（批 83b 收编，两条原独立 beat → sync_config 驱动）：
- `sync_config.id=pool_data`（5 分钟 beat）→ `engine._HANDLERS` handler → 本模块（**增量**）
- `sync_config.id=pool_data_full_calibrate`（周日 04:07）→ 同上（**full=True 全量校准**）
- 手动/定向回补：`scheduler.tasks.pool_data_sync_task`（full 手动 / symbols 入池回补）
sync_log 留痕分工：配置驱动路径由 `engine.sync()` 统一留痕；手动 symbols 路径由
`scheduler.tasks` 调本模块 `log_round()` 留痕（保持原「pool_data 心跳」可观测口径）。

增量语义（U 审项 10，2026-08-20，**逐字保留**）：财务四表按公告日窗口拉取，起点 =
`pool_data_cursor` 表级游标（[cursor, today] 含起点重叠幂等防漏）；无游标首轮回量。
游标推进条件 = 该表本轮覆盖全部池标的（时间盒中断未覆盖的表不推进，下轮重拉同窗口幂等）。
dividend 窗口过滤实测无效（返回 0 行矛盾）维持全量；小表（cyq_chips 只拉当日、季频/事件
表量小）不增量。full=True 强制全量校准（游标照常推进——顺带解冻长期失败冻结的窗口）。
symbols=[...] 定向回补（入池触发）：只跑这些标的、无窗口全量、不推进游标。
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from src.data_platform import db as _pdb
from src.data_platform.rate_limit import rate_limit_context

from .engine import _get_kline_adapter, _provider_of, _read_sync_kind, _get_rate_ds
from .sync_lock import SyncLock

logger = logging.getLogger("data_sync.pool_data")

# 拉取顺序 = 归置表 0106 登记顺序（字典序保序，2026-08-19 原 POOL_DATA_TYPES 同序——
# 逐标的循环顺序影响 API 调用次序，保序免行为漂移）
POOL_TABLES: tuple[str, ...] = (
    "income", "balancesheet", "cashflow", "fina_indicator", "cyq_chips",
    "top10_holders", "dividend", "pledge_stat", "share_float", "stk_holdernumber",
)

# 增量（公告日窗口 [cursor, today]）表：= 财务四表。
# **不从 sync_kind_config.rebuild 派生**：0106 把 cyq_chips 也标 incremental（那是「重建语义」
# 分类），而拉取侧 cyq_chips 是「当日退化窗口、不推游标」——派生会把当日语义换成窗口语义，
# 属行为变更。故此处显式声明（test_pool_specs 断言其 ⊆ 0106 的 incremental 行 = 防漂移）。
_INCREMENTAL_TABLES: frozenset[str] = frozenset(
    {"income", "balancesheet", "cashflow", "fina_indicator"})

# 单日窗口表（源接口按单日取、无区间语义——源侧译成 trade_date=，见 spec["window"]="date"）
_SINGLE_DAY_TABLES: frozenset[str] = frozenset({"cyq_chips"})

# 池数据执行资源锁（**不是** sync_id）——两个 sync_config 行（pool_data /
# pool_data_full_calibrate）必须互斥同一实体资源；若用 sync_id 当锁键，两行各锁各的
# = 5 分钟增量轮与周日全量轮可并发重打上游 API。键与任一 sync_id 都不同（防自锁死：
# engine.sync() 已持 SyncLock(sync_id)，本键若同名则该轮必然拿不到锁）。
_RESOURCE_LOCK = "pool_data_exec"

# 批 92：窗口回看重叠（自然日）。上游公告/财务数据迟发布时游标已推过上界 ⇒ 下轮窗口不再含
# 该日 ⇒ 永久漏。回看 K 天靠 upsert 幂等兜住（增量表窗口 [cursor-K, 上界]）。
_POOL_OVERLAP_DAYS = 7


def _get_pool_ts_codes() -> list[str]:
    """池内 A 股标的（pools.category='astock'）→ Tushare ts_code。"""
    from src.data_platform.schema import vt_to_ts
    with _pdb.get_conn() as conn:
        cur = conn.execute(
            "SELECT DISTINCT ps.symbol FROM pool_symbols ps "
            "JOIN pools p ON p.id = ps.pool_id WHERE p.category='astock'")
        return [vt_to_ts(r[0]) for r in cur.fetchall() if r[0]]


def _load_cursors() -> dict:
    """读全部表级游标 {table: 'YYYYMMDD'}。读失败退化为全量（不阻同步）。"""
    try:
        with _pdb.get_conn() as conn:
            cur = conn.execute("SELECT table_name, last_pull_date FROM pool_data_cursor")
            return {r[0]: r[1] for r in cur.fetchall()}
    except Exception as e:
        logger.warning("游标读取失败（退化为全量）: %s", e)
        return {}


def _advance_cursors(done_symbols: dict, ts_codes: list[str], upper_str: str) -> None:
    """游标推进：增量表本轮覆盖全部标的才推进（防 timebox 中断漏标的）。

    upper_str＝窗口上界（批 92：昨日自然日，与 `_window_of` 同源）。下一轮下界＝
    upper+1-回看重叠（`_window_of` 内叠加），故游标只会**保守前进**，不会越过未覆盖日。
    """
    full_set = set(ts_codes)
    for table in sorted(_INCREMENTAL_TABLES):
        if full_set <= done_symbols.get(table, set()):
            try:
                with _pdb.get_conn() as conn:
                    conn.execute(
                        "INSERT INTO pool_data_cursor (table_name, last_pull_date, updated_at) "
                        "VALUES (%s, %s, now()) "
                        "ON CONFLICT (table_name) DO UPDATE SET "
                        "last_pull_date=EXCLUDED.last_pull_date, updated_at=now()",
                        (table, upper_str))
                    conn.commit()
            except Exception as e:
                logger.warning("游标推进失败 %s: %s", table, e)


def _pool_meta(table: str) -> dict | None:
    """读归置行（kind 维单一真相源）→ {kind, pg_table, pk}；缺行/缺列 → None（响亮跳过）。

    kind/pg_table/pk_cols 全部来自 `sync_kind_config`（0094/0106 立法）——本层零表名、
    零主键硬编码；adapter 侧的列形状声明与归置行由 test_pool_specs 对账（漂移即红）。
    """
    row = _read_sync_kind(table)
    if not row or not row.get("kind") or not row.get("pg_table") or not row.get("pk_cols"):
        return None
    return {"kind": row["kind"], "pg_table": row["pg_table"], "pk": list(row["pk_cols"])}


def _window_of(table: str, cursors: dict, upper_str: str) -> tuple[str | None, str | None]:
    """表级窗口策略（引擎侧编排声明——「请求哪段」；「怎么向源表达」在 adapter）。

    单日表 → (upper, upper)；增量表且有游标 → (cursor-回看重叠, upper) 含起点重叠幂等防漏；
    其余 → (None, None) 全量（不传窗口参数）。

    upper_str＝窗口上界（批 92：由 run_pool_sync 传入的「昨日自然日」，非 today——cron 改每日
    02:00 后当日公告尚未发布，用 today 作上界会让当轮恒拉不到当天公告且游标已越过）。
    """
    if table in _SINGLE_DAY_TABLES:
        return upper_str, upper_str
    if table in _INCREMENTAL_TABLES and cursors.get(table):
        cur = cursors[table]
        start = (date.fromisoformat(f"{cur[:4]}-{cur[4:6]}-{cur[6:8]}")
                 - timedelta(days=_POOL_OVERLAP_DAYS)).strftime("%Y%m%d")
        return start, upper_str
    return None, None


def _pool_request(table: str, ts_code: str, kind: str, window: tuple):
    """构造 fetch 请求（形同 engine._fetch_supply：supply 模式 + 消费者标签 sync）。"""
    from datetime import datetime as _dt

    from src.quant_common.contract import DataRequest
    start, end = window
    rng = (_dt.strptime(start, "%Y%m%d"), _dt.strptime(end, "%Y%m%d")) if start else None
    return DataRequest(kind=kind, symbols=(ts_code,), temporality="historical",
                       sub_kind=table, range_=rng, consumer_tag="sync", mode="supply")


def _sink_frame(frame, meta: dict) -> int:
    """契约帧 → pg_table（引擎层唯一 SQL 出口）。

    列清单 = `frame.columns`（producer 声明的形状）、主键 = 归置行 pk_cols（kind 维）——
    本层零表名/零列名硬编码，异构形状同一路径（多源新增只需 adapter 能产出合规帧）。
    ON CONFLICT 更新面排除 raw_json（对齐原实现：全列存档列不参与增量覆写）。
    逐帧一次 executemany 一次提交（2026-08-21 盘点：逐行 execute 的往返优化保留）。
    """
    rows = list(frame.rows)
    if not rows:
        return 0
    cols = list(frame.columns)
    pk = list(meta["pk"])
    upd = [c for c in cols if c not in pk and c != "raw_json"]
    ph = ", ".join(["%s"] * len(cols))
    sql = (f"INSERT INTO {meta['pg_table']} ({', '.join(cols)}) VALUES ({ph}) "
           f"ON CONFLICT ({', '.join(pk)}) DO ")
    sql += ("UPDATE SET " + ", ".join(f"{c}=EXCLUDED.{c}" for c in upd)) if upd else "NOTHING"
    with _pdb.get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    return len(rows)


def run_pool_sync(cfg: dict | None = None, *, full: bool = False, symbols=None,
                  timebox_s: int = 280) -> dict:
    """跑一轮池内深度同步（通用编排；拉取经 adapter.fetch 契约）。

    cfg=sync_config 行（配置驱动路径；None=手动路径，provider 缺省 tushare）。
    full=True 全量校准（无视游标窗口）；symbols=[ts_code...] 定向回补（无窗口全量、不推游标）。
    时间盒 280s：原 beat 300s 周期 -20s 余量的沿用值；中断即返回 status='timebox'，
    未覆盖表游标不推进（下轮重拉同窗口幂等续跑）。

    Returns: {status: done|partial|timebox|skipped|idle, symbols, saved, errors, duration_ms}
    """
    lock = SyncLock(_RESOURCE_LOCK)
    with lock:
        if not lock.acquired:
            return {"status": "skipped", "reason": "上轮仍在运行", "symbols": 0, "saved": 0,
                    "errors": [], "duration_ms": 0}
        t0 = time.time()
        ts_codes = list(symbols) if symbols else _get_pool_ts_codes()
        if not ts_codes:
            return {"status": "idle", "reason": "无池标的", "symbols": 0, "saved": 0,
                    "errors": [], "duration_ms": int((time.time() - t0) * 1000)}
        provider = _provider_of(cfg)
        # provider→adapter 解析（引擎同款：未知 provider 告警回落兜底源；半成品集成的
        # DataSource 缺位由 _get_rate_ds 抛 ProviderConfigError 穿透=响亮失败，不静默串源）
        adapter = _get_kline_adapter(cfg if cfg is not None else {"provider": provider})
        ds = _get_rate_ds(provider)
        # 批 92：窗口上界＝昨日自然日（cron 改每日 02:00 后当日公告尚未发布）——用 today 会让
        # 当轮恒拉不到当天公告、游标又已越过 ⇒ 静默漏。增量窗口回看重叠在 _window_of 内叠加。
        upper_str = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        cursors = {} if (full or symbols) else _load_cursors()
        deadline = time.time() + timebox_s
        errors: list[str] = []
        metas: dict[str, dict] = {}
        for table in POOL_TABLES:
            meta = _pool_meta(table)
            if meta is None:
                errors.append(f"{table}: 无 sync_kind_config 归置行（kind/pg_table/pk 缺）")
                continue
            metas[table] = meta
        total_saved = 0
        done_symbols: dict[str, set] = {}
        timeboxed = False
        for ts_code in ts_codes:
            for table, meta in metas.items():
                if time.time() >= deadline:
                    timeboxed = True
                    break
                try:
                    req = _pool_request(table, ts_code, meta["kind"],
                                        _window_of(table, cursors, upper_str))
                    # 限速/熔断收编（批 64b 原语义）：键=表名=限速档键；窗口组装与列归一等
                    # 纯计算在上下文外，**只有源拉取**计入配额与熔断（与旧实现同锚点）
                    with rate_limit_context(ds, table):
                        frame = adapter.fetch(req)
                    total_saved += _sink_frame(frame, meta)
                    done_symbols.setdefault(table, set()).add(ts_code)
                except Exception as e:
                    errors.append(f"{ts_code}/{table}: {type(e).__name__}: {str(e)[:40]}")
                    logger.warning("池数据 %s/%s: %s", ts_code, table, e)
            if timeboxed:
                break
        # 游标推进在收尾统一做（按表判覆盖：timebox 中断未覆盖的表下轮重拉同窗口幂等）
        if not symbols:
            _advance_cursors(done_symbols, ts_codes, upper_str)
        duration_ms = int((time.time() - t0) * 1000)
        if timeboxed:
            return {"status": "timebox", "symbols": len(ts_codes), "saved": total_saved,
                    "errors": errors, "duration_ms": duration_ms}
        return {"status": "done" if not errors else "partial", "symbols": len(ts_codes),
                "saved": total_saved, "errors": errors, "duration_ms": duration_ms}


# sync_log 状态词映射（原独立实现口径，收编后逐字保留——见 engine._make_pool_handler）
_ROUND_LOG_STATUS = {"timebox": "timeout", "skipped": "skipped"}


def log_round(result: dict, mode: str = "backfill") -> None:
    """手动/定向回补入口的 sync_log 留痕（配置驱动路径由 `engine.sync()` 统一留痕）。

    口径与原 `pool_data._log` 逐字一致：sync_id='pool_data'、rows_pulled=标的数、
    错误文本落 error 列（**不落 failed_dates** —— `routes/sync.py` 的 progress 端点读 error
    列）；status=timeout（时间盒中断）/skipped（撞锁）/partial（有错误）/done。
    idel（无池标的）**不留痕**（原实现同样为提前返回，不写日志）。
    """
    from .engine import _log
    status = _ROUND_LOG_STATUS.get(result.get("status"),
                                  "partial" if result.get("errors") else "done")
    errors = list(result.get("errors") or [])
    if result.get("status") == "skipped":
        errors = [f"skipped: {result.get('reason', '')}"]
    if result.get("status") == "idle":
        return
    _log("pool_data", mode, "", "", result.get("symbols", 0), result.get("saved", 0),
         result.get("duration_ms", 0), status, "; ".join(errors[:3])[:400], [])
