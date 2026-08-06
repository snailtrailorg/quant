# 09 - 调度层

## 1. 目的

定时任务编排：定时选股、数据增量更新、盘后报告、分时休眠、任务限流。把周期性工作从策略主进程剥离，按计划触发各模块。

## 2. 职责

1. **定时任务注册与执行**：Celery beat 调度，任务路由到对应模块。
2. **数据增量更新**：每日收盘后拉取 A 股日线/分钟线增量、可转债条款、基本面；加密 24h 增量。
3. **定时选股**：盘后触发 A 股日线选股模型。
4. **盘后报告**：每日生成报告（持仓/盈亏/新闻/研报），LLM 网关生成，告警推送。
5. **分时休眠**：A 股/场内非交易时段暂停策略运算，仅保留数据增量，省算力。
6. **任务限流**：Celery 并发限流，防止多任务挤占资源。

## 3. 边界与非目标

- **底层 Celery + Valkey 是第三方**，本模块只定义任务与调度计划。
- **不做**：交易信号实时触发（那是策略 `on_bar` 的事）；实时行情推送（数据中台 WS）。
- **非目标**：不做分布式任务编排平台。

## 4. 依赖

- Celery + Celery beat（第三方）
- Valkey（broker + result backend）
- 数据中台（06）、A 股分析（03）、LLM 网关（01）、告警（10）
- systemd 守护 celery worker + beat

## 5. 接口（任务注册约定）

```python
# Celery 任务，按 name 调度
@task(name="quant.data.increment_daily")
def data_increment_daily():
    """盘后增量更新 A 股日线/分钟线/条款/基本面。"""

@task(name="quant.data.increment_crypto")
def data_increment_crypto():
    """加密 24h K 线增量（每 15min）。"""

@task(name="quant.astock.select_daily")
def astock_select_daily():
    """每日选股，触发 03 模型。非交易日直接 return。"""
    if not is_trading_day(): return       # 数据中台交易日历(来源 Tushare trade_cal)

@task(name="quant.report.daily")
def report_daily():
    """盘后报告：汇总持仓盈亏+新闻研报→LLM网关生成→告警推送。"""

@task(name="quant.astock.minute_analysis")
def astock_minute_analysis():
    """盘中分钟级研判触发（9:30-15:00 内每分钟）。
    任务内判断：非交易日 → return；交易时段外 → return。"""
    if not is_trading_day(): return
    if not in_auction_session(): return    # 9:30-11:30 / 13:00-15:00

@task(name="quant.risk.sweep")
def risk_sweep():
    """定期扫描各账户回撤/亏损，触发自动降级。"""
```

## 6. 调度计划（beat schedule，配置）

```python
# ⚠️ 时区必须显式指定，否则 Beat 默认 UTC，crontab "16:00" 会偏 8 小时
CELERY_TIMEZONE = "Asia/Shanghai"
CELERY_ENABLE_UTC = True          # 内部存 UTC，展示/调度按 Asia/Shanghai

beat_schedule = {
    "data-increment-daily":    {"task":"quant.data.increment_daily",  "crontab": crontab(minute=0, hour=16)},   # 16:00 收盘后
    "data-increment-crypto":   {"task":"quant.data.increment_crypto", "crontab": crontab(minute="*/15")},       # 15min
    "astock-select-daily":     {"task":"quant.astock.select_daily",   "crontab": crontab(minute=30, hour=16)},  # 16:30
    "report-daily":            {"task":"quant.report.daily",          "crontab": crontab(minute=0, hour=18)},   # 18:00
    "astock-minute-analysis":  {"task":"quant.astock.minute_analysis","crontab": crontab(minute="*")},          # 交易时段每分钟
    "risk-sweep":              {"task":"quant.risk.sweep",            "crontab": crontab(minute="*/1")},
}
```

> 交易时段判断：`astock-minute-analysis` 任务内部判断当前是否在 9:30-11:30/13:00-15:00，非时段直接 return。

## 7. 分时休眠

A 股/场内非交易时段（夜间/午休/周末/节假日）：策略进程不退出但暂停 `on_bar` 计算，只保留数据增量与风控扫描。加密不适用（24h）。

通过一个全局"交易日历"（数据中台维护节假日表）判断是否交易时段。

## 8. 任务限流

- Celery worker 并发数限制（如 `-c 2`，符合低配 ECS）。
- 长任务（盘后报告 LLM 生成）给独立队列，不阻塞增量更新。
- 任务超时 kill，失败重试有限次，超限告警。

## 9. 与其它模块交互

- **数据中台**：增量更新任务调 `save_bar`。
- **A 股分析**：选股/分钟研判任务触发模型。
- **LLM 网关**：盘后报告调 `chat(tier=complex)`。
- **告警**：报告/异常推送。
- **风控**：`risk_sweep` 扫描降级。
- **Web 后台**：展示任务状态。

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 调度框架 | Celery + beat | 量化圈标准，vnpy/数据更新都熟 |
| 限流 | worker 并发数 + 队列分离 | 低配资源不挤占 |
| 分时休眠 | 非时段暂停计算 | 省算力，只保留增量+风控 |
| 交易日历 | 数据中台维护节假日表 | A 股/场内时段判断 |
| 加密 | 不休眠，24h | 无时段 |
