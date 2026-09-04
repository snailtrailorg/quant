# ptrade 回测全家桶 · 方案定稿（八步法步 1，吸收双盲审 9 P1）

> 2026-09-04 立项。需求源：`docs/reference/PTradeQuant/回测结果设计-采纳清单.md`。
> 本稿吸收方案盲审 A（P0=0 P1=9 P2=8）+ B（P0=2 P1=5 P2=6）同判比对结论。

## 一、需求（采纳清单 9 项「要补的」，逐项覆盖）

| # | 项 | 覆盖 |
|---|---|---|
| 1 | BacktestResult 扩展指标（α/β/索提诺/信息率/波动率/基准收益） | 批1 |
| 2 | 每日持仓快照 | 批1（avg_price 补入 daily_values） |
| 3 | 回测日志 | 批1 |
| 4 | 基准对比（沪深300） | 批0+批1 |
| 5 | ~~backtest_tasks 缓存表~~ | **不建**——backtest_runs+backtest_symbols 已覆盖「任务+结果」，滚动绩效存 result.metrics.rolling（无冗余表） |
| 6 | 回测 API 分层 | 批2 |
| 7 | 滚动绩效二维表 | 批1 后端算 + 批3 前端 |
| 8 | **导出报告 Excel/PDF** | **批3**（盲审补漏：后端导出端点 + 库 + PDF 中文） |
| 9 | 多标签页前端 | 批3 |

## 二、技术准备（修正盲审指出的 2 处误判）

- ✅ 索提诺/波动率**已实算**（`_calculate` backtest.py:317-320），非默认 0；**α/β/信息率/基准收益**才是默认 0 未算。
- ✅ `avg_price` 引擎主循环**已算出**（backtest.py:187/213/234），只是没 append 进 daily_values——**一行改动**，非"算不出"。
- ✅ trades 已带 commission（采纳清单"补手续费"已过时，无行动）。
- ❌ **指标传播链路全漏（盲审 P1）**：α/β 等算出后，须同步写 ①`BacktestResult.metrics` dict ②`backtest_symbols.result` JSON（`tasks.py:1028` 现只写 5 指标）③`summary_metrics` 聚合 ④`get_backtest_api`/`backtest_summary` 的 `metrics_keys`（routes:324 现 5 键）⑤前端绩效卡。**缺一不可**，否则后端算了前端看不到。

## 三、数据准备（定死，消除"或"）

**基准 = 沪深300**，方案定死：
1. **首选**：`pro.index_daily(ts_code="000300.SH")`——5000 积分档大概率可拉（api-docs 实测 2026-08-19 5000 档），**批0 spike 实测**（index_daily 不在已实测 41 接口里）。
2. **备选（fallback）**：`510300.SH`（沪深300ETF）走既有 `fund_daily` 通道（5000 档稳）——用 ETF 净值代指数，α/β 口径注明跟踪误差。
3. **存储**：独立 `bar_index` 表（**不定死用 bar_1d**——bar_1d 的 `_PER_SYMBOL_META` 只有 astock/etf/cb 三种 kind 无 index，且 `precheck_backtest_data` 品类校验会误判 000300 为股票）。alembic 迁移建表，沿用"运行时不建表"约定。
4. **同步细节**：盘后日更 + 回补起点 2005-04-08（000300 上市日，覆盖长回测）+ 可配基准列表（`system_config` 存 `benchmark_symbols`）。
5. **降级**：基准缺失/窗口不足 → α/β/信息率/基准收益返回 `None`（前端显 `-`），**不崩回测、不填 0 误导**（参照 adj_factor 降级模式）。

## 四、α/β 口径（定死，消除"各表各态"）

1. **收益率口径**：简单收益率 `v[i]/v[i-1]-1`，与现有 sharpe 同口径。
2. **无风险利率**：年化 2%，日化 `0.02/252`，**复用 backtest.py:315 同常数**。
3. **β**：`cov(r_p, r_b) / var(r_b)`，`np.cov(..., ddof=0)`（总体协方差）。
4. **α**：Jensen α = `(mean(r_p) - rf_daily - β*(mean(r_b) - rf_daily)) * 252`（年化）。
5. **信息率**：`mean(r_p - r_b) / std(r_p - r_b, ddof=0) * sqrt(252)`。
6. **基准波动率**：`std(r_b, ddof=0) * sqrt(252) * 100`。
7. **基准收益**：同期累计收益 `(bench_end/bench_start - 1)*100`，**截断到策略回测窗口**。
8. **交易日对齐**：策略 daily_values 与指数 bar 按日期 inner join；错位日（策略停牌）跳过，缺日不参与 cov。
9. **年化因子**：A 股 252；加密 365（多市场，当前只做 A 股基准，加密留 TODO）。
10. **滚动"月度"**：自然月（非 21 日滚动），窗口 1/3/6/12 自然月。

**验收黄金用例**：构造合成基准（已知 β 的随机序列）+ 已知收益序列，手算 α/β 断言数值（防公式错，不只验"非 0"）。

## 五、回测日志机制（盲审 P1：低估，定死）

- 现状：`Strategy` 用模块级 `logger`；`BacktestEngine` monkey-patch `place_order` **绕开** `Strategy.place_order` 的风控 warning；"资金不足"在引擎静默 `adapter.trades.remove()`（216/236 行）零日志。
- 机制三件（最终定为**显式收集器**，非 handler/contextvar/setLevel 隐式状态）：
  1. **run 局部变量收集**：引擎 run 维护 `run_logs` 局部变量 + `def _log_fn`（append），注入 `strategy._log_fn`——显式、零共享状态，进程内并发天然安全。
  2. **引擎侧补记**：资金不足/持仓不足直接 `_log_fn`（不走 logger.warning）。
  3. **策略作者 log API**：`Strategy.log(msg, level)` 优先走 `_log_fn`，无则 fallback 模块 logger（实盘行为保留）。

## 六、方案（分 4 批，批0 spike 先行）

### 批 0（spike，前置闸门）
实测 `pro.index_daily` 5000 档能否拉沪深300 + 列结构；不可则启用 510300.SH ETF fallback。产出：实测结论 + 选定基准源。**不通过不进批1**。

### 批 1（后端：数据 + 指标 + 滚动绩效）
1. 基准同步（pull_index_daily + bar_index 表迁移 + data_sync）
2. `_calculate` 算 α/β/信息率/基准收益/基准波动率（口径见四）
3. daily_values 补 avg_price（一行）
4. 回测日志（机制见五）
5. **滚动绩效**（rolling_metrics 月度 1/3/6/12 窗口 × 指标，存 result.metrics.rolling，不建冗余表）
6. **指标传播链路**（metrics dict / result JSON / summary_metrics / API keys / 前端，见二）

### 批 2（后端 API 分层）
`GET /api/backtest/{run_id}/metrics?type=return|benchmark|alpha|beta|sharpe|sortino|information|volatility|drawdown`——滚动绩效按 type 返回月度×窗口（1/3/6/12）二维表，多标的均值聚合。trades/positions/logs/overview 复用详情端点（result 已含 daily_values/trades/metrics/logs/rolling，数据量小无需冗余端点）。

### 批 3（前端 + 导出）
多标签页（收益概述/交易详情/每日持仓/日志/滚动二维表）+ **导出端点**（Excel openpyxl / PDF reportlab+中文字体，异步 Celery 生成避免卡 web 10s 超时）。

## 七、多标的对齐（盲审 P1）

- α/β 是 **per-symbol** 算（每标的对基准独立算）；`summary_metrics` 聚合用 **均值**（与现有 total_return 聚合一致）。
- 基准 per-symbol 截断到该 symbol 的回测窗口。

## 八、风险（补盲审漏项）

1. index_daily 积分（批0 spike 实测 + 510300 fallback）
2. 导出库依赖 + 中文 PDF 字体（al8 无 CJK 字体 → 豆腐块）+ 异步生成
3. 日志采集不破坏实盘/回测行为（run 作用域隔离）
4. 历史回测兼容：旧 run result JSON 无 α/β，前端容错显 `-`
5. 滚动绩效计算量（回测时算一次存 result.metrics.rolling，前端读缓存不重算）
6. 多市场基准（当前只 A 股沪深300，加密留 TODO）

## 九、制度符合性（W6）

- Schema：bar_index 走 alembic 迁移（不运行时建表）
- 同步：data_sync sync_config 体系
- API：require_perm + ApiError 错误码化
- 导出：Celery 异步（避免 web 超时）

## 十、验收

- 批0：index_daily/ETF 实测结论
- 批1：pytest 全绿 + **α/β 黄金用例数值断言** + 基准落库 + 指标传播链路 5 处一致 + 全量回归
- 批2：新端点结构正确 + overview==metrics==summary 断言 + 旧端点不回归
- 批3：多标签页渲染 + 导出文件可下载 + build/smoke 绿
