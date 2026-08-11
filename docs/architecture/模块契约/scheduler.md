# 模块契约 · scheduler（调度层）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（跨模块签名 + 数据结构）。本文件不重复数据结构定义，只列"本模块暴露什么"。

## 职责
Celery + beat 定时任务编排：数据同步调度 / 盘后选股 / 风控巡检 / 健康探测 / 回测组分发 / 对账与漂移检测。
**所有异步长任务（回测/全量同步/类型同步）经此入 Celery worker**，进度写 Valkey 供前端轮询。A 股任务带交易日/交易时段跳过。

## 文件结构
```
server/src/scheduler/
├── app.py      # Celery 实例 app + 配置 + beat_schedule（定时表）
├── tasks.py    # 所有 @app.task 任务 + _is_trading_day/_is_trading_hours
└── __init__.py # 导出 app + 注册 tasks
```
> 飞书扫码任务在 `feishu_bot/tasks.py`，复用 `scheduler.app.app` 注册（`@celery_app.task`），不在本目录。

---

## 一、public API（稳定，可跨模块调用）

### app.py
```python
app: Celery                        # name="quant", broker/backend=VALKEY_URL
    # include=["src.scheduler.tasks", "src.feishu_bot.tasks"]
# 启动：celery -A src.scheduler.app worker -B -c 2 --loglevel=info
# 配置：timezone="Asia/Shanghai" / enable_utc=True / worker_concurrency=2
#       task_soft_time_limit=300（5min）/ task_track_started=True
# beat_schedule：见下「beat 定时表」
```

### tasks.py（@app.task，web 层用 `.delay()` 触发）
| 任务名（name=） | 触发 | 作用 |
|---|---|---|
| `data_increment_daily` | beat 每天 | 盘后日线增量（`platform.ensure_daily`）；非交易日跳；max_retries=2 |
| `astock_select_daily` | beat 每天 | 每日 A 股选股（`DailySelectionEngine`）；非交易日跳；max_retries=1 |
| `data_increment_crypto` | beat 15min | 加密 K 线增量（占位，待币安/OKX 配置） |
| `data_sync_scheduler` | beat 30min | 扫 `sync_config` 按 cron + 交易日日历触发 `data_sync.sync` |
| `sync_via_celery(sync_id, backfill_from=None)` | web `.delay` | 类型级同步（HTTP 立即返回 task_id；进度写 Valkey `sync:type:{sid}` + task_manager） |
| `sync_all_symbols(sync_id)` | web `.delay` | 全市场全量 per-symbol 同步（`engine.sync_all`；进度 Valkey `sync:progress:{sid}`） |
| `backtest_run_task(run_id)` | web `.delay` | 回测组分发：写 backtest_symbols pending + 按 mode（single/parallel/serial）分发子任务 |
| `backtest_symbol_task(run_id, symbol)` | 内部分发 | 单标的回测子任务：BacktestEngine + on_bar publish Valkey + 存 result |
| `risk_sweep` | beat 1min | 扫风控状态（`is_halted`/`halt_reason`） |
| `daily_report` | （任务） | 盘后报告：LLM 生成（key 未配回退确定性摘要）+ `AlertNotify.report` |
| `health_check` | （任务） | 探测 PG/Valkey/LLM 连通性，异常 `notify("critical")` |
| `drift_check` | beat 每天 | F-MON-005 实盘 vs 回测因子偏差（实盘数据接入后填逻辑，当前 TODO） |
| `reconcile_three_books` | beat 1h | S-ACC-003 信号-委托-成交三账对账（建 signal_log/order_log/trade_log + 比对） |
| `data_continuity_check` | beat 1h | P-MON-006 断连自愈：Valkey 心跳检测 + K 线断点补采 + 因子重算触发 |
| `disk_monitor` | beat 6h | F-OPS-002 磁盘监控（`/`/PG data/Valkey data + PG 库大小），>85% 告警 |
| `task_stuck_check` | （任务） | 卡死检测巡检（`task_manager.detect_stuck`，PT1） |
| `convertible_terms_sync` | beat 每天 | D3 可转债条款同步（`pull_convertible_bonds`+`pull_cb_basic`，存 convertible_terms） |
| `budget_alert_check` | beat 1h | D5 预算告警（交易时段内调 `web_api.check_budget_alerts`） |
| `static_list_sync` | beat 7天 | F-DATA-004 静态标的清单（`stock_basic`，存 static_symbols） |
| `broker_health_check` | beat 6h | #37 通道连通性（遍历 `broker._REGISTRY` 调 `test_connection`），异常告警 |
| `astock_minute_analysis` | （任务） | 盘中分钟研判（占位，待实时行情订阅） |

> beat 定时表完整定义在 `app.conf.beat_schedule`（实盘改 crontab）。每个任务 `options={"queue": "data"/"analysis"/"risk"}` 分队列。

---

## 二、内部 API（不保证稳定，改模块时才能动）

- `_is_trading_day() -> bool`：调 `platform.is_trading_day(date.today())`；异常回退工作日（`weekday() < 5`）
- `_is_trading_hours() -> bool`：A 股连续竞价时段（930-1130 或 1300-1500）；非交易日返回 False
- `sync_all_symbols._mark(status, count)` / `progress_cb(i, total, ts_code)`：进度回调（写 sync_config + Valkey）
- `sync_via_celery.progress_cb(i, total, current)`：进度回调（写 Valkey `sync:type:{sid}` + `update_heartbeat`）
- `backtest_symbol_task.on_bar_cb(bar, ctx)`：每 bar publish Valkey `backtest:run:{run_id}:{symbol}`

> ⚠️ **app.py beat_schedule 缩进错乱**：`"risk-sweep"` 条目（app.py:73）的 `}` 在文件末尾（:98），其间 `convertible-terms-sync`/`budget-alert-check`/`static-list-sync`/`broker-health-check` 四条按 `{}` 匹配**实际嵌套在 risk-sweep dict 内**，beat 顶层不会调度这四条。疑似合并残留——**TODO 核实修复**（修后本表 beat 列需重核）。

---

## 三、依赖（import 其他模块什么）

| 本文件 | 依赖 | 用途 |
|---|---|---|
| tasks.py | `data_platform.db`（get_conn/is_trading_day/save_bars）/ `data_platform.platform` | PG + 交易日 + 补采 |
| tasks.py | `data_platform.adapters.tushare_adapter` | pull_daily/to_save_rows/pull_convertible_bonds/pull_cb_basic/get_pro |
| tasks.py | `astock_analysis.DailySelectionEngine` | 每日选股 |
| tasks.py | `risk_control.RiskControl` | risk_sweep / daily_report |
| tasks.py | `llm_gateway.gateway` | daily_report / health_check（`gateway.chat`） |
| tasks.py | `alert_notify.AlertNotify` | 多任务告警出口 |
| tasks.py | `data_sync.engine`（sync/sync_all）/ `data_sync.sync` | 同步执行 |
| tasks.py | `task_manager`（create_task/update_heartbeat/complete_task/log_task/notify_on_failure/detect_stuck） | 异步任务统一管理 |
| tasks.py | `strategy_framework`（StrategyConfig/BacktestEngine） | 回测子任务 |
| tasks.py | `web_api.main.check_budget_alerts` | 预算告警（函数内 import） |
| tasks.py | `strategy_framework.broker._REGISTRY` | 通道健康检查 |
| app.py | celery / croniter（外部）/ dotenv | 调度框架 + cron 解析 + .env |

> ⚠️ `scheduler` → `web_api.main`（check_budget_alerts）是**反向依赖**（web_api 反过来被 scheduler 调），靠函数内 import 延迟打破。改签名注意。

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `web_api.main` | `sync_via_celery.delay` / `sync_all_symbols.delay`（同步端点）/ `backtest_run_task.delay`（回测端点，TODO 核实）/ `celery_app`（revoke 终止任务）/ `reconcile_three_books`（手动触发对账） |
| `feishu_bot.tasks` | `from src.scheduler.app import app as celery_app`（注册 `feishu_register_task`） |

> 改任务 `name=` 字符串影响 web `.delay()` 调用——保持 `src.scheduler.tasks.<func>` 命名稳定。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `sync_config` | `sync_all_symbols._mark`（last_status/last_sync_count）/ `data_sync_scheduler`（last_status 跳 running） | `data_sync_scheduler`（schedule/last_sync_ts/trade_day_filter） |
| `strategy_config` | — | `drift_check`（enabled+backtest_verified）/ `backtest_symbol_task`（factors/aggregator/params） |
| `backtest_runs` | `backtest_run_task`（status=running/task_id）/ `backtest_symbol_task`（status=done/finished_at） | 两个回测任务（strategy_config_id/symbols/params/mode） |
| `backtest_symbols` | `backtest_run_task`（INSERT pending）/ `backtest_symbol_task`（status=done/error+result） | `backtest_symbol_task`（count pending 判 run 完成） |
| `bar_1D` | `data_continuity_check`（断点补采 `save_bars`） | `data_continuity_check`（近 7 天 cnt 检测） |
| `convertible_terms` | `convertible_terms_sync`（CREATE+UPSERT） | — |
| `static_symbols` | `static_list_sync`（CREATE+UPSERT） | — |
| `signal_log`/`order_log`/`trade_log` | `reconcile_three_books`（CREATE TABLE IF NOT EXISTS 兜底） | `reconcile_three_books`（三账比对） |

**Valkey 键**（前端轮询 / 跨任务通信）：
- `sync:progress:{sid}` / `sync:type:{sid}`（同步进度 hash）
- `backtest:run:{run_id}:{symbol}` / `:done` / `:error`（回测实时 + 结果）
- `heartbeat:gateway`（数据_continuity 网关心跳检测）/ `factor:recalc:triggered`（因子重算触发标记）

---

## 六、不变量

- **时区**：`Asia/Shanghai` + `enable_utc=True`；A 股任务用本地时间判交易时段
- **并发限流**：`worker_concurrency=2`（低配 ECS）；重任务（回测/全量同步）单独放宽 `soft_time_limit=3600`/`time_limit=4200`
- **默认超时**：`task_soft_time_limit=300`（5min），超时 Celery 杀任务
- **A 股任务跳过**：`_is_trading_day`/`_is_trading_hours` 返回 False → 返回 `{"status":"skipped","reason":...}`，不抛
- **进度写 Valkey**：所有长任务（同步/回测）进度 hash + expire（1h），前端轮询；完成态存结果字段
- **任务纳入统一管理**：`sync_via_celery`/`backtest_run_task` 调 `task_manager.create_task` + `update_heartbeat` + `complete_task`（卡死检测 + 告警联动）
- **任务命名**：`name="src.scheduler.tasks.<func>"`（web `.delay()` 靠名字路由，勿改）

---

## 七、扩展指南

### 加新定时任务
1. tasks.py 加 `@app.task(name="src.scheduler.tasks.<func>", bind=True)` 函数
2. app.py `beat_schedule` 加 `"<key>": {"task": "...", "schedule": <秒>, "options": {"queue": "..."}}`
3. 需长耗时：加 `soft_time_limit=`/`time_limit=` 覆盖默认 5min
4. 需进度：写 Valkey hash + expire（复用 `sync:progress:{sid}` 模式）
5. 需任务管理：开头 `create_task` + 心跳 + 收尾 `complete_task`

### 加新队列
1. app.py 任务 `options={"queue": "<new>"}`
2. 启动单独 worker：`celery -A src.scheduler.app worker -Q <new> -c 2`

---

## 修订记录
- 2026-08-11 初版（基于代码核实：app.py:1-99 / tasks.py:1-794 / __init__.py 全读；beat_schedule 缩进问题待核实）
