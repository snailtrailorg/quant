# 模块契约 · data_platform（数据中台）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件，不用读整个项目。
> 配套：`docs/architecture/接口契约.md`（跨模块签名 + 数据结构）。本文件不重复数据结构定义，只列"本模块暴露什么"。

> **最近变更（2026-09-24 D1/D3/加密接入批）**：`perms.account_allows`（D1）；`db.get_bars(source)` 按 source 过滤（D3）；`get_interface_row(md_only)` 跳过 required_fields（加密 MD 空凭证）。
## 职责
统一数据中台：PG 连接池 + K 线 schema + Tushare 拉取 + 交易日历 + 数据源抽象。
**所有模块通过本模块访问数据**（`get_conn`/`save_bars`/`get_bars`/`is_trading_day`），不直接接触数据源。

## 文件结构
```
server/src/data_platform/
├── db.py              # PG 连接池 + K 线读写（validate_bars）+ 交易日历 + verify_schema
├── store.py           # 批 59 M4：Store（版本冻结骨架 cur_version/frozen_or_current/save）
├── databus.py         # 批 59/60a M4：DataBus 消费门面（get_bars/get/get_snapshot/subscribe 真实现 + _watermark 连续无缺；StreamHandle 流句柄；读 Valkey hub:bars:* 依赖）
├── schema.py          # Bar dataclass + vt_symbol 转换 + DDL 模板
├── data_source.py     # DataSource 接口（get_param*/get_rate_limit）+ Tushare/AkShare 实现（DB 化凭证 + 积分档预设）
├── rate_limit.py      # 限流+熔断三件套：RateLimiter/CircuitBreaker/rate_limit_context（2026-08-27 限流治理新建）
├── _platform.py      # DataPlatform 单例（统一入口，部分占位；批9 改名自 platform.py——包属性/子模块/实例三层同名占用 import 机制保留地，懒加载下现投毒竞态，守门=tests/test_data_platform_lazy.py）
├── settings.py        # 环境变量集中读取
├── audit.py           # audit_log（原寄生 web_api.auth，2026-08-19 归位）
├── perm_registry.py   # 批33b：权限资源注册表（唯一字面量层——api 13 键+nav 19 条+market 5+别名；DB 覆盖层 perm_resource 四字段；绑定扫描 router-walk 175 处+防漂移闸）
├── market_snapshot.py # 三档腾讯实时快照（quote:tencent 60s TTL）
├── stock_detail.py    # 三档详情聚合层（quote 降级链+慢变块缓存）
├── routing.py         # 批 57 M2：路由内核（resolve 五步/bulkhead/审计/热更新对账）——见下节
├── security_master.py # 批 56a：SMClient（标的属性库读侧+engine 填充链写侧）+MarketHours（节奏域）——见下节
├── tz.py             # 批 56b：as_utc（写/读 PG 统一收口——naive 按上海解释转 UTC aware）+as_shanghai（显示层）
├── schema_expectations.txt  # verify_schema 期望基线（迁移链生成物，禁手写）
└── adapters/
    └── tushare_adapter.py  # Tushare 拉取 + DataFrame->rows 转换 + 质量校验
```
（P3 回写 2026-08-20：补 audit/market_snapshot/stock_detail/schema_expectations.txt 四文件）

---

## 批33b 新模块：perm_registry.py（2026-09-17）

- **唯一字面量层**：`API_PERM_KEYS`（13——55b account_keys 退役）/`NAV_ITEMS_BASE`（19 含 perm-resources+routing 批 57）/`MARKET_OP_KEYS`（5）/`NAV_ALIASES`（活别名 stock/data-manage——批39 死别名 8 条已清）；perms.py 单向 import（admin 集=注册表派生）。
- **DB 覆盖层**：`perm_resource` 表（迁移 0084）只改显示四字段（group/order/label per-locale/enabled）——条目集恒代码单源红线（PATCH 仅 nav kind+id∈注册表预检）；`load_registry()` 容错回底座不缓存。
- **绑定扫描**：`scan_perm_bindings()` router-walk（FastAPI 0.141 懒 include——app.routes 零 APIRoute，扫 APIRouter 实例 175 处）；`check_binding_drift()` 键集⊆注册表防漂移闸（web_api startup 落点）。
- GET `/api/perm-resources`（system_config）三段+绑定反查；PATCH `/api/perm-resources/{kind}/{id}`（四键全量显式）。

## 批 57 新模块：routing.py（2026-09-20，29 号 §五 M2）

- **resolve(req, principal=None) -> CandidateChain**：五步——硬过滤（external_interface.capabilities
  （55a 语义名经 _CAP_KIND_ALIASES 映射）+enabled+SMClient.covers scope+权限 market 级）/软排序
  （健康一票前置（熔断沉底——28 §6.1 张力裁决）>position 人工序>三因子加权）/审计落 routing_decision
  /供给模式排除 local_pg（28 §7.1 v3.2）/local_pg 消费模式恒第一候选（LOCAL_KINDS 起步={bar_daily}）。
- **CandidateChain.fetch(fetch_fn, req, skip)**：链式下移（SourceUnavailable→failover 审计下移/
  DataGap 记尾注全尽抛/bulkhead 超闸 skip_busy/熔断 skip_unhealthy/deadline_ms 超时）。
  fetch_fn 回调注入——M4 门面接线（本批只选不拉）。
- **bulkhead**：Valkey 计数 `routing:bh:{adapter}`（TTL 60s 兜底；被拒内部回减）；阈值=
  routing_policy.bulkhead_defaults 覆写>派生初值（M3+ Quality 档案真实化）。
- **热更新**：`cfg:version` Valkey 单调整数（策略页保存 INCR bump_config_version）+TTL 2s 周期对账
  （pub/sub 订阅通道挂 M4/M5）。
- 读写表：routing_policy/routing_decision（读写）；external_interface（读）。
- 被调：web_api /api/routing/*（策略 CRUD+dry-run+审计）；engine._get_kline_adapter 试点
  （system_config 键 routing_kline_pilot=on 时 resolve(supply) 选源；off=原路径回滚）。

## 批 56a 新模块：security_master.py（2026-09-20，29 号 §四）

- **SMClient**：`get(vt_symbol)->SecurityAttr|None` / `effective_attr(vt,kind,at)->dict|None`（effective_from<=at 最新行）/ `covers(scope,symbols)->bool`（单查 ANY 批量取档，库内无档按交易所后缀近似）/ `upsert_rows`+`upsert_state`（**engine 填充链唯一写通道**——executemany 单批 18 号 §2.1；品类值+生命周期列随行携带，`ON CONFLICT` 字段级更新，list_date 空值 COALESCE 不抹旧；web 侧零写）。
- **MarketHours**：`sessions(session_id,at)->list[Phase]`（进程内缓存，无行不缓存）/ `day_anchor(session_id)->datetime`（表驱动 market_hours.anchor+tz，返今天 aware 锚点）/ `is_auction(vt,at)`（scope 板块大小写归一+trade_cal 日历感知，日历缺年 fail-open）/ `band_of(exchange,board,is_st)`（band_rules 规则派生）。HTTP 面经 `get_market_hours()` 单例。
- 读写表：security_master / security_state / market_hours / band_rules（读）；security_master / security_state（写，仅 engine 链）。
- 被调：engine 填充链三 handler+namechange 派生（写）；web_api `/api/security/{vt_symbol}`（读）；M2 resolve 接 covers（批 57）。

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
    # to_regclass 校验 bar_{freq} 存在+告警，不建表（建表归迁移 0064，运行时不再 CREATE TABLE）
save_bars(freq: str, rows: list[tuple]) -> int
    # 批量写 K 线，ON CONFLICT DO NOTHING（冲突跳过）。返回 len(rows)
    # rows 11 字段：(symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)
    # 开头已接线 rows = validate_bars(rows)（save_bars/save_bars_overwrite 开头；A2 已实现，P3 回写 2026-08-20 撤"A2 待改"）
save_bars_overwrite(freq: str, rows: list[tuple]) -> int
    # 批量写，ON CONFLICT DO UPDATE（回补覆盖）。返回 len(rows)
validate_bars(rows: list[tuple]) -> list[tuple]
    # A2 已实现（db.py:82）：剔 ohlc=0 行 + 标 ts 断点 warning（不剔）；save_bars/save_bars_overwrite 开头调用
    # 批 56b 写收口：入口统一 ts=as_utc（naive 按上海解释——pin UTC 后裸 naive 进库=错 8h 唯一生死面）
get_bars(symbol: str, freq: str, start, end) -> pd.DataFrame
    # 查 K 线，列：symbol/freq/ts/open/high/low/close/volume/amount/adj_factor/source
    # 批 56b 读收口：start/end 为 naive datetime 时 as_utc（get_kline_records/get_index_bars 同款）
    # 连接 pin：TimeZone=UTC（28 §3.2 立法——绝对时刻表示统一；epoch 校验和双会话恒同实证零数据变更）
get_trade_calendar(year: int) -> list[date]
    # 从 trade_cal 表读 SSE 交易日（is_open=1）
is_trading_day(d: date | None = None) -> bool
    # d 默认今天；查 trade_cal，查不到回退工作日
init_trade_calendar(year: int) -> None
    # no-op（表在 migration 0001；运行时 DDL 清零后保留签名兼容，db.py:211）（P3 回写 2026-08-20，原"建表幂等"过时）
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

# SQL 模板（.format(freq=...) 填充；建表 DDL 归迁移 0064，schema.py 不再保留 BAR_TABLE_DDL）：
BAR_TABLE_INSERT       # INSERT ... ON CONFLICT DO NOTHING
BAR_TABLE_INSERT_OVERWRITE  # INSERT ... ON CONFLICT DO UPDATE
BAR_TABLE_SELECT       # SELECT ... WHERE symbol=%s AND ts BETWEEN %s AND %s
```

### data_source.py（DataSource 接口，详见接口契约 §1.1）
```python
DataSource(ABC)
    __init__(credentials_encrypted: str | None = None, params: str | None = None)
        # params = 运维参数 JSON（分界：秘密→credentials_encrypted；points_tier/rate_limits/
        # rate_time_overrides/circuit_breaker/base_url→params）
    get_client() / test_connection()                    # abstract
    get_param(*keys, default=None)                      # （新增 2026-08-27）命名空间路径读 params：
        # get_param("circuit_breaker", "fail_threshold")；路径断/中途非 dict 回 default 不抛
    get_param_float(*keys, default: float, lo: float, hi: float) -> float
        # （新增 2026-08-27）读 float + 范围钳位：非法回落 default+告警，越界钳 [lo, hi]
        # ——防呆护栏在后端，不信任 DB 手写值
    get_rate_limit(api_name: str) -> float              # 两次调用最小间隔秒（0=不限）。基类三级：
        # DEFAULT_RATE_LIMITS → params.rate_limits → params.rate_time_overrides 时段乘数
        # （interval /= multiplier，>1=更快；窗口支持跨零点 "22:00-02:00"，首条命中即生效）
    record_usage(api_calls=1, api_name="", success=True, latency_ms=0, provider="")
        # 写 data_source_usage 表（失败不抛）；provider 缺省从 self.provider 取
TushareDataSource(DataSource)    # token DB 优先 .env fallback
    DEFAULT_RATE_LIMITS          # 类级默认 8 档（stk_mins 3600 / daily 0.5 / adj_factor 0.3 ...，= 200 积分现状实测）
    POINTS_PRESETS               # （新增 2026-08-27 积分档批次）{200/2000/5000: {api: 间隔秒}}——
        # 官方积分档频控换算表；Web 下拉选档存 params.points_tier，官方调限额改这里走部署
    get_rate_limit(api_name)     # 覆写基类，四层解析（L0←L1←L2←L3，后者覆盖前者）：
        # L0 DEFAULT_RATE_LIMITS（代码兜底最保守）← L1 params.points_tier 选中档预设批量覆盖
        # ← L2 params.rate_limits 单参数覆写（应急调个别接口）← L3 时段乘数
        # points_tier 不存在=三级老行为向后兼容；非法值各层独立回落+告警不崩同步
AkShareDataSource(DataSource)    # stub 未注册 _REGISTRY（东财被反爬弃用，三档 U-2 教训）
get_data_source(provider: str) -> DataSource | None    # 工厂（DB enabled 行实例化），无配置返回 None
```

### rate_limit.py（限流+熔断三件套，2026-08-27 限流治理吸收新建，`docs/obsolete/任务归档/限流治理吸收.md`）
```python
class CircuitOpenError(RuntimeError)
    # 熔断打开抛出——engine 循环捕获记 failed_dates 下轮续（幂等），不重试不打爆

class RateLimiter:
    # 线程安全最小间隔执行器：「占位」语义（并发第二者排在上次占位+间隔后，不踩踏）
    __init__(interval=0.0, clock=time.monotonic, sleep=time.sleep)  # clock/sleep 可注入（测试假时钟）
    acquire(api_name="", interval: float | None = None) -> float
        # 阻塞至距上次调用满间隔，返回实际等待秒（首次=0）；interval 显式传入则覆盖
        # （rate_limit_context 每次现取四层值刷新——时段乘数随时段变化）
    set_interval(interval)        # 非法值按 0=不限

class CircuitBreaker:
    # 三态 Closed→Open（连续失败≥阈值）→Half-open（reset_timeout 到点只放一次探测）
    # →成功回 Closed / 失败再 Open。DataSource 级（D2：Tushare 配额共享体，任何接口打穿封整个账号）
    __init__(fail_threshold=None, reset_timeout=None, clock=..., ds=None)
        # ds 有则从 ds.get_param("circuit_breaker", ...) 读参（显式实参 > params 配置 >
        # 代码默认 fail_threshold=5 / reset_timeout=60s；走 get_param_float 钳位语义，DB 垃圾值不崩）
    state -> "closed"|"open"|"half_open"
    allow() -> bool               # Open 未到 reset_timeout 拒；到点转 Half-open 放一次探测
    record_success() / record_failure()

rate_limit_context(ds, api_name: str, min_interval: float | None = None)   # @contextmanager
    # 声明式：with rate_limit_context(ds, "daily"): pull_daily(...)
    # 进 = 熔断检查（Open→raise CircuitOpenError）+ 间隔等待（ds.get_rate_limit(api_name) 现取；
    #   min_interval 显式覆盖 = engine sleep_s 兼容路径）
    # 出 = 成败入账（异常先 record_failure 再原样上抛）
    # 进程内注册表 _LIMITERS[(provider, api_name)] / _BREAKERS[provider]（单实例内存计数，不做分布式）
reset_registries()               # 测试隔离用；运行期勿调（丢熔断记忆）
```
> 设计决策 D1：限速在 engine 侧（编排节奏），adapter pull_* 零改动。

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
pull_cb_basic(ts_code) -> dict                  # 可转债条款（D3）（P3 回写 2026-08-20 补）
get_daily_symbols() -> list[str]                # 当日有行情的标的清单（P3 回写 2026-08-20 补）
pull_adj_factor_by_date(trade_date) / pull_adj_factor_by_code(ts_code, start, end)
    # 复权因子（by_date 全市场批 / by_code 单标的；adj_factor_backfill 用）（P3 回写 2026-08-20 补）
# 三档一档 9 个 pull（tier1 handler 工厂按名取用，2026-08-19）：
pull_stk_limit / pull_moneyflow / pull_margin_detail / pull_top_list
pull_block_trade / pull_cyq_perf / pull_forecast / pull_namechange / pull_concept
    # 签名 pull_x(trade_date, end_date=None)；namechange/concept 全量重建式（P3 回写 2026-08-20 补）
# 转换（DataFrame -> rows 11 字段元组）：
to_save_rows(df, freq="1D") -> list[tuple]      # 日线（trade_date 列作 ts）
to_save_rows_min(df, freq) -> list[tuple]       # 分钟线（trade_time 列作 ts）
# 入库：
save_daily_basic(df) -> int                     # 写 daily_basic 表
# 质量校验：
validate_bar_quality(df) -> dict                # {valid, issues, clean_count, ...}
```

> （2026-08-27 回写：原注"get_rate_limit 三级 + AkShare 已注册"已并入上方 data_source.py 块——限速现四层（积分档批次），AkShare 实为未注册 stub）

### market_snapshot.py（三档项 13，非池实时价）
```python
get_quote(ts_code, force=False) -> dict | None
    # 腾讯 qt.gtimg.cn 单股按需（U-2 实施选型，2026-08-20：akshare 东财被反爬 RST）
    # 返回 {ts, name, last, pct_chg, volume(手), amount(万), high/low/open/pre_close,
    #       upper_limit/lower_limit, turnover_rate, pe, float_mv/total_mv(亿),
    #       bid/bid_v/ask/ask_v(五档), source: "tencent"}
    # Valkey quote:tencent:{ts_code} 60s TTL；源不可达返回 None（调用方降级）
```

### stock_detail.py（三档项 14+17，详情聚合层）
```python
get_stock_detail(symbol) -> dict
    # 任意格式 symbol 归一 → {symbol, ts_code, name, industry, in_pool, limit,
    #   moneyflow(近5日), events(龙虎榜/大宗/解禁/质押合并 20 条), name_changes,
    #   chips(池内直读/非池按需), finance(池内直读/非池按需), quote}
    # quote 降级链：hub:latest_tick:{vt}（秒级）→ 腾讯(60s TTL) → null；不缓存
    # 慢变块 Valkey detail:slow:{ts} 10min——完整块才缓存（部分降级不落防缺块 10min）
    # 非池按需 detail:ondemand:{kind}:{ts} 5min（"null" 字串缓存空结果防穿透）
    # 永不抛异常：各块独立降级，坏块 null/[]
    # 被 web_api GET /api/stock/{symbol}/detail（薄壳）与 POST /analyze 调用
```

### _platform.py（DataPlatform 单例 `platform`；批9 改名自 platform.py，防 import 同名投毒）
```python
platform.get_bar(symbol, freq, start, end, adj="qfq") -> pd.DataFrame
platform.ensure_daily(ts_code, start_date, end_date=None, adj="qfq") -> int
platform.ensure_minute(ts_code, freq, start_date, end_date=None) -> int
platform.is_trading_day(d=None) -> bool
platform.get_trade_calendar(year) -> list[date]
platform.init_calendar(year) -> None            # 调 tushare.pull_trade_cal
# 占位（T04/T05 实现）：get_realtime / get_fundamental / get_convertible_terms / get_funding_rate
# subscribe 已真实现（批 60a）——在 databus.DataBus.subscribe（非 platform 单例），流消费经 StreamHandle
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
| data_source.py | `src.quant_common.crypto.decrypt` | 解密 DB 凭证（2026-08-19 归位后路径；原 `web_api.crypto_utils` 循环依赖已解）（P3 回写 2026-08-20） |
| rate_limit.py | （纯 stdlib：threading/contextlib/time，零跨模块依赖） | 限流+熔断自包含（2026-08-27 新增行） |
| tushare_adapter.py | `.schema.to_vt_symbol` | ts_code -> vt_symbol |
| _platform.py | `.db` / `.schema` / `.adapters.tushare_adapter` | 组合 |
| settings.py | dotenv | 读 .env |

> 曾有 `data_platform` ⇄ `web_api.crypto_utils` 循环依赖——2026-08-19 模块归位后加解密在 `quant_common.crypto`（层 0），循环已解。（P3 回写 2026-08-20）

---

## 四、被谁调用（改 public API 签名要同步改这些）

| 调用方 | 调什么 |
|---|---|
| `data_sync.engine` | `get_conn` / `save_bars` / `save_bars_overwrite` / `get_data_source`（_get_pro）/ **`rate_limit.rate_limit_context`（5 处拉取点：_sync_by_trade_date / _sync_astock_minute / backfill_adj_factor / tier1 handler / sync_all，2026-08-27）** |
| `data_sync.pool_minute` | `get_data_source` + `get_rate_limit("stk_mins")`（Valkey 全局闸门取间隔） |
| `strategy_framework.backtest` | `get_bars` |
| `astock_analysis.analysis` | `get_bars` / `is_trading_day` |
| `web_api.main` | `get_conn`（大量端点）/ `get_bars`（K线端点） |
| `web_api.routes.mgmt` | `_REGISTRY` / `DEFAULT_RATE_LIMITS` / `POINTS_PRESETS` / `get_rate_limit` / `get_param_float`（积分档三端点 GET points-presets / POST points-tier / POST rate-limit-override，2026-08-27 数据源管理页） |
| `risk_control.risk` | `get_conn`（check_order / account_snapshot） |
| `scheduler.tasks` | `get_conn` / `is_trading_day` / `platform` |
| `llm_gateway.gateway` | `get_conn`（llm_usage 写） |
| `alert_notify.notify` | `get_conn` |
| `task_manager` | `get_conn`（tasks/task_logs） |
| `im_bot.feishu_client`（经 `feishu_bot` 薄壳） | `get_conn`（im_bot_config / im_bot_users 读，2026-08-21 起替代原 feishu_config 直读） |

> 改 `get_conn` / `save_bars` / `get_bars` 签名影响**几乎全项目**--慎改，优先加新函数不破旧。

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `bar_1D` / `bar_1min` / `bar_5min` | `db.save_bars` / `save_bars_overwrite` | `db.get_bars`（回测/分析/K线端点） |
| `daily_basic` | `tushare_adapter.save_daily_basic` | web_api（筛选端点） |
| `trade_cal` | `tushare_adapter.pull_trade_cal` | `db.get_trade_calendar` / `is_trading_day` |
| `external_interface`（批55a 合表 0090：data_source_config+broker_config→统一外部接口表——行=账号/列=能力/页签=过滤视图，见 27 号架构文档） | web_api（/api/interfaces 族+旧端点垫片，写侧 ⊆ 代码能力校验） | `data_source.get_data_source`（数据域行）/`broker.get_broker`（交易域行） |
| `data_source_usage` | `DataSource.record_usage`（API 用量，A4 #36；批55a 加 interface_id 列——provider 双填保留显示冗余） | web_api（用量看板，GROUP BY provider 不变） |
| `channel_config` | web_api（消息通道端点） | `channel.get_channel` |
| `live_trading_config` | web_api（实盘开关端点） | `risk.is_live_trading_allowed` |
| `account_snapshot` | `risk.update_account_snapshot`（幂等建） | `risk._get_global_state` |
| `llm_usage` | `gateway._log_usage`（幂等建） | web_api（用量看板） |

> 三档数据 19 张新表（stk_limit 等 9 张一档 + income 等 10 张二档）由 data_sync/pool_data 经本模块 adapter 拉取写入，详见 [17-三档数据与详情页](../../design/D17-三档数据与详情页.md)。
> schema 唯一真相源=alembic 迁移链（`server/migrations/versions/`，**head 0090**；**运行时零 DDL**——2026-08-13 起 CREATE TABLE IF NOT EXISTS 已全部入迁移）；启动校验 `db.verify_schema()` 对 `schema_expectations.txt`（**91 表**生成式基线，"表 :: 列"每表一行；每加迁移必重跑生成命令并提交——批55a 链重生成时浮出批11C/43/47/53 等多批欠账一并还清）。

---

## 六、不变量

- **vt_symbol**：`raw.EXCHANGE`（`600000.SHSE`），转换走 `schema.to_vt_symbol/to_ts_code`
- **freq**：`1D` / `1min` / `5min` / `15min` / `30min` / `60min`（bar 表后缀 = freq）。**写入口径以 `db._VALID_FREQS` 白名单为准（db.py:119，2026-08-20 扩）**：11 项超集 `{'1min','5min','15min','30min','60min','1h','4h','1d','1H','4H','1D'}`（大小写兼容）——`schema.Freq` Literal 是其子集，两者非同一集合（P3 回写 2026-08-20 注明）
- **ts**：`TIMESTAMPTZ`，A 股 +08:00，加密 UTC
- **rows 11 字段顺序**：`(symbol, freq, ts, open, high, low, close, volume, amount, adj_factor, source)`--`save_bars`/`save_bars_overwrite`/`to_save_rows`/`to_save_rows_min` 一致
- **get_conn**：`with` 退出还池；不手动 close
- **save_bars**：`ensure_table` 校验表存在+告警（建表归迁移 0064，不再运行时 DDL）
- **is_trading_day**：查 trade_cal，查不到回退工作日（不抛）
- **stk_mins**：per-symbol 接口（不支持按日全市场），2000 积分，单次 8000 条（超限分段，见 engine._split_minute_range）
- **限流四层**（批55a 勘察定稿，详单 `docs/obsolete/任务归档/批55a-限流四层牵连勘察.md`）：**L1** `DEFAULT_RATE_LIMITS` 类默认（代码兜底）← **L2** `params.rate_limits` 单参数覆写（24 号去积分档后两级）；非法值回落+告警不崩同步。**L3 熔断** DataSource 级（D2），参数 `params.circuit_breaker`（代码默认 fail_threshold=5 / reset_timeout=60s）；进程内键=provider——同 provider 多账号共享熔断/限速（勘察发现 3 裂缝，深水区批解）。**L4** `data_source_usage` 用量（provider+interface_id 双填）
- **params 分界**：秘密→`credentials_encrypted`；运维参数（points_tier/rate_limits/rate_time_overrides/circuit_breaker/base_url）→`params` JSON；数值一律经 `get_param_float` 钳位（不信任前端/DB 手写值）

---

## 七、扩展指南

### 加新数据源（如 Wind）
1. 实现 `DataSource` 子类（`get_client`/`test_connection`）
2. `data_source._REGISTRY["wind"] = WindDataSource` + `markets.PROVIDER_MARKET["wind"]` 登记（层 0 纯数据）
3. Web 配 `external_interface`（批55a 统一表：provider='wind'，market，capabilities=启用子集，credentials 加密）
4. 不改 engine（`_get_pro` 走 `get_data_source`）
5. 限速可选：子类设 `DEFAULT_RATE_LIMITS` 即自动进 `rate_limit_context` 体系，engine 拉取点零改动

### 加新 K 线频率（如 15min）
1. migration 建 `bar_15min` 表（0064 的 `_create_bar_table` 模式，结构同 bar_1d）
2. `_PER_SYMBOL_META` 加一行（如 `astock_minute_15min`）
3. `save_bars("15min", rows)` 自动工作（表已由迁移建）

### 加新表（如财务指标）
1. migration 建表 + DDL 兜底在 handler
2. `tushare_adapter` 加 `pull_<x>` + `to_save_<x>_rows` + `save_<x>`
3. `data_sync.engine` 加 handler + `_HANDLERS` 注册
4. `init-seed.sql` 加 sync_config 种子

---

## 修订记录
- 2026-08-09 初版（基于代码核实：db/schema/data_source/tushare_adapter/platform/settings 全读）

- audit_log 已入本模块（data_platform/audit.py，原寄生 web_api.auth——feishu_bot 曾因此反向 import 顶层）
- 2026-08-27 回写：限流治理吸收三件套（rate_limit.py 新建：RateLimiter/CircuitBreaker/rate_limit_context）+ 积分档四层（data_source.py：get_param/get_param_float/POINTS_PRESETS 200/2000/5000/get_rate_limit L0-L3）；补 data_source_usage 表、mgmt 积分档三端点被调；schema head 0049→0053（74 表）

## 最近变更
- 2026-08-27 限流治理吸收 + 积分档预设四层限流（`docs/obsolete/任务归档/限流治理吸收.md`；双盲补审 fa1f123 全修后产上部署）

> **批55-0(2026-09-19)能力查询层**:新增 `capabilities.py`(`provider_capabilities`/`check_capability_subset`——能力真源=代码 `_ADAPTERS` sync_id 串经 `quant_common.markets.SYNC_ID_CAP_MAP` 归一;配置能力⊆代码校验,55a 端点写侧与漂移告警消费)。维度注册表本体在 `quant_common/markets.py`(层 0 纯数据——本模块只放读上层代码的查询函数,分层铁律)。立法全文 `docs/architecture/A02-外部接口与市场维度设计.md`。
