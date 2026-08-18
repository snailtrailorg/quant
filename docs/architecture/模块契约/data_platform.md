# 模块契约 · data_platform（数据中台）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（跨模块签名 + 数据结构）。本文件不重复数据结构定义，只列"本模块暴露什么"。

## 职责
统一数据中台：PG 连接池 + K 线 schema + Tushare 拉取 + 交易日历 + 数据源抽象。
**所有模块通过本模块访问数据**（`get_conn`/`save_bars`/`get_bars`/`is_trading_day`），不直接接触数据源。

## 文件结构
```
server/src/data_platform/
├── db.py              # PG 连接池 + K 线读写 + 交易日历
├── schema.py          # Bar dataclass + vt_symbol 转换 + DDL 模板
├── data_source.py     # DataSource 接口 + TushareDataSource（DB 化凭证）
├── platform.py        # DataPlatform 单例（统一入口，部分占位）
├── settings.py        # 环境变量集中读取
└── adapters/
    └── tushare_adapter.py  # Tushare 拉取 + DataFrame->rows 转换 + 质量校验
```

---

## 一、public API（稳定，可跨模块调用）

### schema 校验（#48 v2，2026-08-18）
```python
load_schema_expectations() -> dict[str, set[str]]   # 读 schema_expectations.txt（链生成物，禁手写）
verify_schema() -> {"missing_tables": [...], "missing_columns": {t: [c]}, "expectations_missing"?: True}
    # 纯函数单向存在性（expected ⊆ actual，不比型不比多余列）；单条 information_schema 查询。
    # 告警路由归入口层（health_monitor.report_schema_findings）——db 层不引告警依赖。
    # 四入口接线：web startup / strategy_runner / md_hub / celery 父进程
```
- **期望基线生成命令**（每加迁移必跑并提交，见 db.py docstring）：scratch schema 跑链 → dump → DROP SCHEMA
- 不变量：`save_bars_overwrite` 与 `save_bars` 同款校验（大小写不敏感 freq + validate_bars）

### db.py
```python
get_conn() -> psycopg.Connection
    # 连接池（pool_size=10 + max_overflow=20，pre_ping，recycle=1800s）
    # ⚠️ with 退出还池；保留 psycopg 裸 SQL 风格（conn.execute/cursor/commit）
get_engine() -> sqlalchemy.Engine
    # 给 alembic / pandas.read_sql / to_sql 用
ensure_table(freq: str) -> None
    # CREATE TABLE IF NOT EXISTS bar_{freq}（DDL 模板见 schema.py）
save_bars(freq: str, rows: list[tuple]) -> int
    # 批量写 K 线，ON CONFLICT DO NOTHING（冲突跳过）。返回 len(rows)
    # rows 11 字段：(symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)
    # A2 待改：开头加 rows = validate_bars(rows)
save_bars_overwrite(freq: str, rows: list[tuple]) -> int
    # 批量写，ON CONFLICT DO UPDATE（回补覆盖）。返回 len(rows)
get_bars(symbol: str, freq: str, start, end) -> pd.DataFrame
    # 查 K 线，列：symbol/freq/ts/open/high/low/close/volume/amount/adj_factor/source
get_trade_calendar(year: int) -> list[date]
    # 从 trade_cal 表读 SSE 交易日（is_open=1）
is_trading_day(d: date | None = None) -> bool
    # d 默认今天；查 trade_cal，查不到回退工作日
init_trade_calendar(year: int) -> None
    # 建 trade_cal 表（幂等），数据由 tushare_adapter.pull_trade_cal 写
```

### schema.py
```python
Bar  # dataclass，见接口契约 §Bar
Freq = Literal["1min", "5min", "15min", "1H", "4H", "1D"]

to_vt_symbol(ts_code: str) -> str          # "600000.SH" -> "600000.SHSE"
to_ts_code(vt_symbol: str) -> str          # "600000.SHSE" -> "600000.SH"
parse_vt_symbol(vt_symbol: str) -> tuple[str, str]  # -> ("600000", "SHSE")

TS_EXCHANGE_MAP = {"SH":"SHSE", "SZ":"SZSE", "BJ":"BSE", ...}  # Tushare->vnpy
EXCHANGE_TS_MAP = {...}                                       # 反向

# DDL/SQL 模板（.format(freq=...) 填充）：
BAR_TABLE_DDL          # CREATE TABLE bar_{freq} ...
BAR_TABLE_INSERT       # INSERT ... ON CONFLICT DO NOTHING
BAR_TABLE_INSERT_OVERWRITE  # INSERT ... ON CONFLICT DO UPDATE
BAR_TABLE_SELECT       # SELECT ... WHERE symbol=%s AND ts BETWEEN %s AND %s
```

### data_source.py（DataSource 接口，详见接口契约 §1.1）
```python
DataSource(ABC): get_client() / test_connection() / record_usage(api_calls=1)
TushareDataSource(DataSource)   # token DB 优先 .env fallback
get_data_source(provider: str) -> DataSource | None   # 工厂，无配置返回 None
```

### tushare_adapter.py
```python
get_pro() -> tushare.pro_api        # 全局单例（token 从 .env，注意：不经 DataSource DB）
# 拉取：
pull_daily(ts_code, start_date, end_date=None, adj="qfq") -> pd.DataFrame
pull_minute(ts_code, freq, start_date, end_date=None) -> pd.DataFrame
    # ⚠ stk_mins 需 2000 积分；start_date 格式 "YYYYMMDD HH:MM:SS"
    # trade_time 列格式 "YYYY-MM-DD HH:MM:SS"
pull_cb_daily(start_date, end_date=None) -> pd.DataFrame
pull_trade_cal(year: int) -> list[tuple]        # 写 trade_cal 表
pull_convertible_bonds() -> list[str]           # ts_code 列表
pull_etf_list() -> list[str]
pull_daily_basic(ts_code, start_date, end_date=None) -> pd.DataFrame  # PE/PB/市值
# 转换（DataFrame -> rows 11 字段元组）：
to_save_rows(df, freq="1D") -> list[tuple]      # 日线（trade_date 列作 ts）
to_save_rows_min(df, freq) -> list[tuple]       # 分钟线（trade_time 列作 ts）
# 入库：
save_daily_basic(df) -> int                     # 写 daily_basic 表
# 质量校验：
validate_bar_quality(df) -> dict                # {valid, issues, clean_count, ...}
```

### platform.py（DataPlatform 单例 `platform`）
```python
platform.get_bar(symbol, freq, start, end, adj="qfq") -> pd.DataFrame
platform.ensure_daily(ts_code, start_date, end_date=None, adj="qfq") -> int
platform.ensure_minute(ts_code, freq, start_date, end_date=None) -> int
platform.is_trading_day(d=None) -> bool
platform.get_trade_calendar(year) -> list[date]
platform.init_calendar(year) -> None            # 调 tushare.pull_trade_cal
# 占位（T04/T05 实现）：get_realtime / subscribe / get_fundamental / get_convertible_terms / get_funding_rate
```
> ⚠️ `platform` 单例职责是"统一入口"，但当前多数模块直接用 `db.save_bars`/`get_bars`/`get_conn`（不绕 platform）。新代码可优先用 platform，旧代码保留。

### settings.py
```python
is_live_trading_enabled() -> bool   # .env ENABLE_LIVE_TRADING（实盘第一级开关）
```

---

## 二、内部 API（不保证稳定，改模块时才能动）

- `db._engine`：SQLAlchemy engine 单例（`get_conn`/`get_engine` 的底层）
- `tushare_adapter._pro`：tushare pro 全局缓存（`get_pro` 用）
- `tushare_adapter._safe_float(v, default=0.0)`：安全转 float（None/NaN/空串 -> default）
- `data_source._REGISTRY` / `broker._REGISTRY` / `channel._REGISTRY`：provider->类 注册表

---

## 三、依赖（import 其他模块什么）

| 本文件 | 依赖 | 用途 |
|---|---|---|
| db.py | sqlalchemy / psycopg（外部） | 连接池 + 裸 SQL |
| data_source.py | `src.web_api.crypto_utils.decrypt` | 解密 DB 凭证 |
| tushare_adapter.py | `.schema.to_vt_symbol` | ts_code -> vt_symbol |
| platform.py | `.db` / `.schema` / `.adapters.tushare_adapter` | 组合 |
| settings.py | dotenv | 读 .env |

> ⚠️ `data_platform` 依赖 `web_api.crypto_utils`（解密）--`web_api` 反过来依赖 `data_platform`（get_conn）。这是**循环依赖**，当前靠函数内 import 延迟打破。改签名注意。

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `data_sync.engine` | `get_conn` / `save_bars` / `save_bars_overwrite` / `get_data_source`（_get_pro） |
| `strategy_framework.backtest` | `get_bars` |
| `astock_analysis.analysis` | `get_bars` / `is_trading_day` |
| `web_api.main` | `get_conn`（大量端点）/ `get_bars`（K线端点） |
| `risk_control.risk` | `get_conn`（check_order / account_snapshot） |
| `scheduler.tasks` | `get_conn` / `is_trading_day` / `platform` |
| `llm_gateway.gateway` | `get_conn`（llm_usage 写） |
| `alert_notify.notify` | `get_conn` |
| `task_manager` | `get_conn`（tasks/task_logs） |
| `feishu_bot.bot` | `get_conn`（feishu_config 读） |

> 改 `get_conn` / `save_bars` / `get_bars` 签名影响**几乎全项目**--慎改，优先加新函数不破旧。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `bar_1D` / `bar_1min` / `bar_5min` | `db.save_bars` / `save_bars_overwrite` | `db.get_bars`（回测/分析/K线端点） |
| `daily_basic` | `tushare_adapter.save_daily_basic` | web_api（筛选端点） |
| `trade_cal` | `tushare_adapter.pull_trade_cal` | `db.get_trade_calendar` / `is_trading_day` |
| `data_source_config` | web_api（数据源端点） | `data_source.get_data_source` |
| `broker_config` | web_api（交易通道端点） | `broker.get_broker` |
| `channel_config` | web_api（消息通道端点） | `channel.get_channel` |
| `live_trading_config` | web_api（实盘开关端点） | `risk.is_live_trading_allowed` |
| `account_snapshot` | `risk.update_account_snapshot`（幂等建） | `risk._get_global_state` |
| `llm_usage` | `gateway._log_usage`（幂等建） | web_api（用量看板） |

> 各 handler 内含 `CREATE TABLE IF NOT EXISTS` 兜底（新库容错）；正式 schema 走 alembic migration（0001/0013/0014/0015/0016/0017/0022）。

---

## 六、不变量

- **vt_symbol**：`raw.EXCHANGE`（`600000.SHSE`），转换走 `schema.to_vt_symbol/to_ts_code`
- **freq**：`1D` / `1min` / `5min` / `15min` / `30min` / `60min`（bar 表后缀 = freq）
- **ts**：`TIMESTAMPTZ`，A 股 +08:00，加密 UTC
- **rows 11 字段顺序**：`(symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)`--`save_bars`/`save_bars_overwrite`/`to_save_rows`/`to_save_rows_min` 一致
- **get_conn**：`with` 退出还池；不手动 close
- **save_bars**：`ensure_table` 兜底建表（新 freq 安全）
- **is_trading_day**：查 trade_cal，查不到回退工作日（不抛）
- **stk_mins**：per-symbol 接口（不支持按日全市场），2000 积分，单次 8000 条（超限分段，见 engine._split_minute_range）

---

## 七、扩展指南

### 加新数据源（如 Wind）
1. 实现 `DataSource` 子类（`get_client`/`test_connection`）
2. `data_source._REGISTRY["wind"] = WindDataSource`
3. Web 配 `data_source_config`（provider='wind'，credentials 加密）
4. 不改 engine（`_get_pro` 走 `get_data_source`）

### 加新 K 线频率（如 15min）
1. migration 建 `bar_15min` 表（复用 `BAR_TABLE_DDL.format(freq="15min")`）
2. `_PER_SYMBOL_META` 加一行（如 `astock_minute_15min`）
3. `save_bars("15min", rows)` 自动工作（`ensure_table` 兜底）

### 加新表（如财务指标）
1. migration 建表 + DDL 兜底在 handler
2. `tushare_adapter` 加 `pull_<x>` + `to_save_<x>_rows` + `save_<x>`
3. `data_sync.engine` 加 handler + `_HANDLERS` 注册
4. `init-seed.sql` 加 sync_config 种子

---

## 修订记录
- 2026-08-09 初版（基于代码核实：db/schema/data_source/tushare_adapter/platform/settings 全读）
