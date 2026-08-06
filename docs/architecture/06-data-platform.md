# 06 - 数据中台

## 1. 目的

统一三市场数据口径，**杜绝回测与实盘数据偏差**。拉取 → 清洗 → 存储 → 实时推送，全市场统一 schema，支撑所有模型运行。关键设计：**回测历史数据 schema 现在就和未来 XTP 实时行情对齐**，20 天等待期回测结果零迁移切 live。

## 2. 职责

1. **数据源适配**：Tushare Pro（主，A 股日线+分钟线需 2000 积分）/ AkShare（免费补 + 实时增量）/ 币安·OKX WebSocket（加密）。
2. **清洗**：统一复权、停牌填充、异常价格过滤，规避前视偏差、幸存者偏差。
3. **存储**：PostgreSQL（业务+中低频时序 K 线+pgvector 向量）+ Valkey（实时盘口/分钟 K/策略状态/队列）。
4. **实时推送**：订阅行情 → 策略 `on_bar/on_tick`。
5. **统一 schema**：K 线 OHLCV、符号编码、复权标记、时间戳，回测与实盘一致。

## 3. 边界与非目标

- **弃用** QUANTAXIS（停更+兼容坑）；弃用 TimescaleDB / 重型向量库（轻量化）。
- **不做**：Tick 级高频存储（按需订阅，不持久化全量 Tick）；不做行情转售。
- **非目标**：不做跨交易所深度套利数据（只 BTC/ETH 主流）。

## 4. 依赖

- Tushare Pro（付费积分，分钟线需 2000）/ AkShare（免费）/ 币安·OKX 公开 API
- PostgreSQL 18 + pgvector + Valkey（第三方）
- 调度层（09）：数据增量定时任务
- Celery（第三方）任务队列

## 5. 接口

```python
class DataPlatform:
    # 历史
    def get_bar(self, symbol: str, freq: str, start: date, end: date,
                adj: str = "qfq") -> DataFrame: ...
    # 实时
    def get_realtime(self, symbol: str) -> Quote: ...          # 走 Valkey
    def subscribe(self, symbol: str, freq: str, handler: Callable[[Bar], None]) -> None: ...
    def unsubscribe(self, symbol: str, freq: str) -> None: ...
    # 写入
    def save_bar(self, symbol: str, freq: str, df: DataFrame) -> int: ...
    # 基本面/条款
    def get_fundamental(self, symbol: str) -> dict: ...
    def get_convertible_terms(self, symbol: str) -> dict: ...   # 强赎/下修/回售
    def get_funding_rate(self, symbol: str) -> float: ...
    # 交易日历（A 股节假日判断，调度层依赖）
    def get_trade_calendar(self, year: int) -> list[date]: ...   # 来源 Tushare trade_cal
    def is_trading_day(self, d: date | None = None) -> bool: ...  # 调度任务跳过非交易日
    # 数据源适配
    def register_source(self, name: str, adapter: DataSourceAdapter): ...
```

## 6. 数据 schema（核心：回测/实盘对齐）

### 6.1 K 线表（PostgreSQL `bar_{freq}`，分区按年）
| 字段 | 类型 | 说明 |
|---|---|---|
| symbol | text | **vnpy 原生 vt_symbol**：`{raw_symbol}.{EXCHANGE}`，exchange 用 vnpy Exchange 枚举值。如 `603986.SHSE` / `113xxx.SZSE` / `BTCUSDT-PERP.BINANCE`。**严禁点号在前格式**（如 `SH.603986` 会导致 XTP 实盘"未知合约"拒单）|
| freq | text | `1min`/`5min`/`15min`/`1H`/`4H`/`1D` |
| ts | timestamptz | **统一带时区**（A股+08:00，加密 UTC）|
| open/high/low/close | numeric | OHLCV |
| volume | numeric | 成交量 |
| amount | numeric | 成交额 |
| adj_factor | numeric | 复权因子（A 股）|
| source | text | tushare/akshare/binance/okx/xtp |

**对齐原则**：XTP 实时行情推送的字段名/类型/编码与这张表完全一致，实盘把 WS 回调写入同样结构，策略代码读到的 `Bar` 对象回测实盘同形。symbol 统一用 vnpy `vt_symbol`（`raw.EXCHANGE`），XTPAdapter 用 `parse_vt_symbol()` 拆成 `(symbol, exchange)` 再喂 XTP 网关（详见 04）。具体 exchange 串（`SHSE`/`SZSE`/...）以 vnpy_xtp 枚举为准，实现时核实。

### 6.2 业务表（PG）
- `account` / `strategy_config` / `order` / `trade` / `position` / `astock_analysis` / `llm_usage` / `risk_log`

### 6.3 向量表（pgvector）
- `doc_embedding`：研报/公告/可转债条款的向量化，供 RAG 检索（LLM 网关 embed 写入）。

### 6.4 实时（Valkey）
- `quote:{symbol}`：最新盘口/价格
- `bar:{symbol}:{freq}`：当前未完成 K 线
- `strategy_status:{id}`：策略运行状态
- 队列：Celery + 行情订阅分发

## 7. 数据流

```
Tushare/AkShare ──定时增量──> 清洗(复权/停牌/异常) ──> PG bar表
币安/OKX WS ─────实时──────> 清洗(异常过滤) ──> Valkey实时 + PG增量
订阅: DataPlatform.subscribe(symbol, freq, handler)
  └> Valkey 推 bar ─> handler(bar) ─> 策略.on_bar
```

## 8. 清洗规则

- **复权**：A 股前复权（qfq）为主，存原始+复权因子，回测按需取。
- **停牌**：停牌期间用前收盘填充并在 `source` 标记，回测可过滤。
- **异常价格**：超涨跌停/0 价格 → 过滤告警。
- **前视偏差**：只用当日收盘后可得的数据做日线回测，分钟线严格按时间戳。
- **幸存者偏差**：选股回测用当时点的成分股，不用最新指数成分。

## 9. 与其它模块交互

- **策略框架（02）**：`get_bar`/`subscribe` 喂策略；`Bar` 对象 schema 对齐实盘。
- **A股分析（03）/可转债ETF（04）/加密（05）**：都是数据消费者。
- **LLM 网关（01）**：`embed()` 写 pgvector，RAG 检索喂 LLM。
- **调度层（09）**：定时增量更新任务。
- **Web 后台（08）**：看数据覆盖/质量。

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 数据源 | Tushare 主 + AkShare 补 | Tushare 稳但要积分，AkShare 免费补缺/实时 |
| 分钟线积分 | 凑到 2000（一次性购买可接受） | 200 分拿不到分钟线，已核实 |
| 存储 | PG 统一（弃 TimescaleDB） | 轻量化，中低频够用 |
| 向量库 | pgvector | 复用 PG，不引 Milvus |
| 实时 | Valkey | 已有，复用 |
| schema 对齐 | 现在就对齐 XTP 实时 | 零迁移是核心设计 |
| QUANTAXIS | 弃用 | 停更+坑，自建清洗层替代 |
