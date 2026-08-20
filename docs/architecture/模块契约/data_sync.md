# 模块契约 · data_sync（数据同步引擎）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件。
> 配套：`docs/architecture/接口契约.md`（跨模块签名 + SyncResult 结构）。

## 职责
通用增量/全量同步引擎，按 `sync_config.id` 调度。支持三种粒度：
- **type 级**（`sync`）：按 sync_id 路由 handler，全量/增量/回补
- **per-symbol**（`sync_symbol`/`backfill_symbol`/`delete_symbol`）：单标的完整性驱动
- **全市场**（`sync_all`）：Celery 后台逐只全量

完整性驱动（非游标）+ Valkey 心跳锁防重（进程被杀 TTL 自愈）。

## 文件结构
```
server/src/data_sync/
├── engine.py        # 同步引擎核心 + _HANDLERS 注册表 + _make_tier1_handler 工厂（三档一档）
├── pool_data.py     # 池内深度数据同步（三档二档，独立模块不进 _HANDLERS）
├── pool_minute.py   # 池分钟同步（Tushare stk_mins 收费，beat 注释禁用态）
├── sync_lock.py     # Valkey 心跳锁
└── __init__.py      # 导出 sync/sync_symbol/backfill_symbol/delete_symbol/sync_all
```

---

## 一、public API（稳定）

### engine.py
```python
sync(sync_id: str, backfill_from: str | None = None,
     progress_cb: Callable | None = None) -> dict
    # 类型级同步：按 sync_id 路由 _HANDLERS[sync_id]
    # backfill_from=YYYYMMDD：回补历史（不推进 last_sync_date 游标）
    # progress_cb(i: int, total: int, current: str)：进度回调
    # 返回 SyncResult dict（见接口契约 §SyncResult）：{status, pulled, saved, failed_dates, ...}
    # 防重：SyncLock 抢锁失败 -> {status:"skipped", reason:"上次同步仍在运行"}

sync_symbol(sync_id: str, ts_code: str, mode: str = "auto") -> dict
    # per-symbol 智能同步（完整性驱动）
    # mode='auto'：空 -> 从上市日起全量；有 -> _find_gaps 找缺口段逐段补
    # mode='full'：从上市日起全量
    # 返回 {status, pulled, saved, range:[首,末], mode_used, gaps_filled?, local_count_before?}
    # status: success/empty/uptodate/error

backfill_symbol(sync_id: str, ts_code: str, start: str, end: str) -> dict
    # per-symbol 回补：用户指定范围，DO UPDATE 覆盖本地
    # 返回 {status, pulled, saved, range, overwritten:True}

delete_symbol(sync_id: str, ts_code: str) -> dict
    # 删单标的本地数据（日线删 bar_1D，分钟线删 bar_1min/5min）
    # 返回 {status:"success", deleted:int, symbol:str}

sync_all(sync_id: str, progress_cb: Callable | None = None) -> dict
    # 全市场全量（Celery 调，逐只 sync_symbol mode='full'）
    # 返回 {status, total, ok, failed_count, saved, failed:list}

list_symbols(sync_id: str, q: str = "", page: int = 1, size: int = 9999) -> dict
    # 列标的 + 本地数据状态（批量聚合查 bar 表，一次 ANY 查询非逐只）
    # 返回 {items:[{ts_code, name, list_date, local_count, local_first, local_last}], total}
    # （P3 回写 2026-08-20：此两行原错位漂到 pool_data 代码块尾，归位）

backfill_adj_factor(start_date: str | None = None, end_date: str | None = None) -> dict
    # 复权因子回填（A/B-F1：bar_1D 历史全 NULL）——pull_adj_factor_by_date 全市场逐日拉，
    # UPDATE bar_1D.adj_factor；积分未到账降级返回 {"status":"degraded"} 不抛（engine.py:535）
    # 被 scheduler.adj_factor_backfill_task（web 手动 .delay）调用（P3 回写 2026-08-20 补）
```

### tier1 handler 工厂（三档一档，17 号文档 §3）
```python
_make_tier1_handler(table, pull_fn_name, pk_cols, float_cols=None, text_cols=None) -> handler
    # 工厂生成 batch_date 逐日拉取 handler，注册进 _HANDLERS[sync_config.id]
    # 两模式：批量日期循环（游标增量+backfill_from 含当日）与全量重建（namechange/concept 整表换）
    # upsert：insert_cols = pk + 非pk；placeholders 必须与列同长（曾漏 PK 致每日静默失败）
    # forecast 按 ann_date 拉非 trade_date（handler 按 pull 函数签名自动区分）
```

### pool_data.py（三档二档，池成员驱动非 sync_config）
```python
sync_pools_data(timebox_s: int = 280, full: bool = False, symbols: list[str] | None = None) -> dict
    # 池内 astock 标的 × 10 类深度数据（财务四表/筹码分布/十大股东/分红/质押/解禁/股东人数）
    # _get_pool_ts_codes(): pools JOIN pool_symbols WHERE category='astock'（出池自动停更）
    # 时间盒到即收工下轮续 + SyncLock("pool_data") 防重叠 + 必写日志护栏（FATAL/skipped 也落 sync_log）
    # 增量（2026-08-20）：财务四表窗口 [cursor, today]（含起点重叠幂等），游标 pool_data_cursor
    #   表级（迁移 0047）；推进=该表覆盖全部标的；dividend 窗口实测无效维持全量
    # full=True 全量校准（游标照常推进）；symbols=[ts] 定向回补（入池触发，无窗口不推进游标）
    # 返回 {status: done|partial|timebox|skipped|idle|error, symbols, saved, errors[:5]}
    # 被 scheduler.pool_data_sync_task（beat 300s + 周日 full 校准）与 web
    #   POST /api/sync/pool-data/trigger?full= 与入池端点（symbols 回补）调用
```

### sync_lock.py
```python
class SyncLock:
    SyncLock(sync_id: str, ttl: int = 60)   # key = f"sync:lock:{sync_id}"
    .acquire() -> bool                      # SET NX EX 抢锁（token 校验）
    .start_heartbeat() -> None              # 后台线程每 20s 刷 TTL（留 40s 余量）
    .heartbeat() -> None                    # 手动刷一次（无后台线程时）
    .release() -> None                      # 只删自己的（token 校验）
    .acquired: bool                         # 是否抢到
    # 上下文管理器：with SyncLock(sid) as lock: ...
    # ⚠️ 进程被杀 -> TTL 60s 自然过期 -> 下次能抢（不卡死）
```

---

## 二、内部 API（不保证稳定，改模块时才能动）

### 路由/元数据
- `_PER_SYMBOL_META: dict[str, tuple]` - `{sync_id -> (freq, table, kind, bar_type)}`，5 条（3 日线 + 2 分钟线）
- `_PER_SYMBOL_SYNC_IDS = set(_PER_SYMBOL_META)`
- `_HANDLERS: dict[str, Callable]` - `{sync_id -> type 级 handler}`，**19 条**（engine.py:601 基础 10 + 三档一档 9：`_TIER1_BATCH` 7 批量 + `_TIER1_FULL` 2 全量重建，循环注册）（P3 回写 2026-08-20：原"10 条"为一档上线前数量）
- `_MINUTE_FREQ = {"astock_minute":"1min", "astock_minute_5min":"5min"}`
- `_TUSHARE_MIN_DATE` - 全量起点（.env SYNC_START_DATE，默认 20100101）

### 分钟线（A1 新增）
- `_split_minute_range(start, end, freq) -> list[tuple]` - stk_mins 8000 条分段（1min 33天/段，5min 166天/段）
- `_fetch_minute_and_save(ts_code, freq, start, end, overwrite=False) -> (df, saved)` - 分段拉取入库
- `_wrap_minute_result(df, used, cnt, start, end) -> dict`

### per-symbol 内部
- `_get_pro_api(sync_id) -> (pro, api_fn, kind, freq, bar_type)`
- `_list_static_ts_codes(kind) -> list[str]` - 从静态表取 ts_code
- `_get_list_date(kind, ts_code) -> str` - 查上市日（回退 _TUSHARE_MIN_DATE）
- `_local_bar_range(vt, table="bar_1D") -> (first, last, cnt)` - 本地首末日+条数
- `_local_trade_dates(vt, table="bar_1D") -> list[str]` - 本地交易日（distinct，YYYYMMDD）
- `_expected_trade_dates(api_fn, start, end) -> list[str]` - trade_cal 算预期交易日
- `_find_gaps(api_fn, kind, ts_code, first, last, table="bar_1D") -> list[tuple]` - 缺口段

### 日线内部
- `_fetch_and_save(api_fn, ts_code, start, end, save_fn) -> df | None`
- `_wrap_result(df, used, cnt, start, end) -> dict`
- `_daily_to_rows(df, adj_map: dict | None = None) -> list[tuple]` / `_save_bars(rows) -> int` / `_daily_to_save_fn(df) -> int`
    # adj_map 第二参：按 ts_code 映射复权因子（回填链路用）（P3 回写 2026-08-20 补）

### type 级 handler（基础 10 个 sync_id 9 函数 + tier1 工厂 9 个）
- 基础：`_sync_astock_daily` / `_sync_astock_basic` / `_sync_astock_list` / `_sync_cb_daily` / `_sync_cb_basic` / `_sync_etf_daily` / `_sync_etf_list` / `_sync_trade_cal` / `_sync_astock_minute`（1min+5min 共用）
- tier1（`_make_tier1_handler`/`_make_full_rebuild_handler` 批量注册 9 个 sync_id，见 17 号 §3）：stk_limit_sync / moneyflow_sync / margin_detail_sync / top_list_sync / block_trade_sync / cyq_perf_sync / forecast_sync / namechange_sync / concept_sync（P3 回写 2026-08-20 补）

### 通用工具
- `_get_pro()` - 从 data_source DB 读 Tushare（.env fallback）
- `_sync_by_trade_date(pro_api_fn, save_fn, start, end, sleep_s, progress_cb) -> dict` - 按日批量拉取
- `_log(sync_id, mode, start, end, pulled, saved, duration_ms, status, error, failed_dates, expected_days, actual_days)` - 写 sync_log
- `_mark_running(sync_id, running)` / `_get_config(sync_id) -> dict` / `_update_sync_state(sync_id, last_date, count)`
- `_expected_trading_days(start, end) -> int`

---

## 三、依赖（import 其他模块什么）

| 依赖 | 用途 |
|---|---|
| `data_platform.db` | `get_conn` / `save_bars` / `save_bars_overwrite` |
| `data_platform.adapters.tushare_adapter` | `pull_daily`/`pull_minute`/`pull_cb_daily`/`pull_trade_cal`/`to_save_rows`/`to_save_rows_min`/`save_daily_basic` |
| `data_platform.data_source` | `get_data_source`（`_get_pro` DB 化） |
| `data_platform.schema` | `to_vt_symbol` |
| `sync_lock.SyncLock` | 防重心跳锁 |
| 外部 | pandas / redis / croniter（scheduler 用，本模块不直接） |

---

## 四、被谁调用

| 调用方 | 调什么 |
|---|---|
| `web_api.main` | `sync`（trigger_sync_api）/ `sync_symbol` / `backfill_symbol` / `delete_symbol` / `sync_all` / `list_symbols`（6 个 per-symbol 端点 + progress） |
| `scheduler.tasks.sync_via_celery` | `sync(sync_id, backfill_from, progress_cb)` |
| `scheduler.tasks.sync_all_symbols` | `sync_all(sync_id, progress_cb)` |
| `scheduler.tasks.data_sync_scheduler` | `sync(sid)`（按 cron 触发） |

> 改 `sync`/`sync_symbol` 签名影响 web_api 端点 + scheduler Celery 任务，慎改。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `sync_config` | `_mark_running`/`_update_sync_state`（last_status/last_sync_date/last_sync_ts/last_sync_count） | `_get_config` |
| `sync_log` | `_log`（每次同步结果） | web_api（sync/log 端点） |
| `bar_1D`/`bar_1min`/`bar_5min` | `save_bars`/`save_bars_overwrite`（经 _fetch_and_save/_fetch_minute_and_save） | `_local_bar_range`/`_local_trade_dates`/`list_symbols`（聚合查） |
| `asset_static_info`/`etf_basic_info`/`cb_basic_info` | 各 list handler（全量写） | `_list_static_ts_codes`/`_get_list_date`/`list_symbols` |
| `trade_cal` | `_sync_trade_cal`（pull_trade_cal） | `_expected_trade_dates`/`_expected_trading_days` |
| `daily_basic` | `_sync_astock_basic`（save_daily_basic） | - |
| `data_source_config` | - | `_get_pro`（经 get_data_source） |
| 一档 9 表（stk_limit/moneyflow/margin_detail/top_list/block_trade/cyq_perf/forecast/namechange/concept） | tier1 handler 工厂（batch/全量重建） | - |
| 二档 10 表（income/balancesheet/cashflow/fina_indicator/cyq_chips/top10_holders/dividend/pledge_stat/share_float/stk_holdernumber） | `pool_data._upsert_rows`（幂等 upsert） | 详情页三档已实施（`stock_detail` 池内直读：筹码/财务块）（P3 回写 2026-08-20：原"未实施"过时） |
| `pool_data_cursor` | `pool_data._advance_cursors`（游标推进，迁移 0047） | `pool_data._load_cursors` |

---

## 六、不变量

- **sync_id**：∈ `_PER_SYMBOL_META`（per-symbol 支持）或 `_HANDLERS`（type 级）
- **SyncResult dict 格式**：见接口契约 §SyncResult（status/pulled/saved/failed_dates/...）
- **progress_cb 签名**：`(i: int, total: int, current: str)`（type 级 current 是 trade_date 或 ts_code）
- **防重**：`SyncLock`（Valkey 心跳锁，TTL 60s，后台 20s 刷）；进程被杀 TTL 过期自愈；`last_status` 只展示，**不作防重依据**
- **回补**：`backfill_from` 不推进 `last_sync_date` 游标（只补历史）
- **空状态跳过**：`last_sync_date=NULL` 的 per-symbol 类型，`data_sync_scheduler` 跳过增量（避免首跑全市场超时）
- **分钟线**：stk_mins per-only（不支持按日全市场）+ 8000 条分段（`_split_minute_range`）
- **per-symbol 缺口**：按交易日粒度找（`_find_gaps`，trade_cal 比对本地 distinct date）；分钟线缺口段再按 stk_mins 限制分小段
- **vt_symbol / freq / ts**：见接口契约 §五 不变量

---

## 七、扩展指南

### 加新 type 级同步（如 etf_minute）
1. `engine.py` 加 `_sync_etf_minute` handler
2. `_HANDLERS["etf_minute"] = _sync_etf_minute`
3. `init-seed.sql` 加 `sync_config` 种子
4.（如 per-symbol）`_PER_SYMBOL_META` 加 `(freq, table, kind, bar_type)`

### 加新 per-symbol 类型
1. `_PER_SYMBOL_META[sync_id] = (freq, table, kind, bar_type)`
2. 确保 kind 对应静态表存在（astock/etf/cb）
3. `sync_symbol`/`backfill_symbol`/`delete_symbol`/`list_symbols` 自动支持（按 bar_type 分支）

### 加新数据源（经 DataSource）
1. `data_platform.data_source` 实现子类 + 注册
2. `_get_pro` 自动经 `get_data_source`（不改 engine）

---

## 修订记录
- 2026-08-09 初版（基于 engine.py + sync_lock.py 全读核实）
