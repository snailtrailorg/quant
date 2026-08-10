# 03 - A股分析引擎（可实盘，受 astock 分项开关控制）

## 1. 目的

A股个股的**纯分析**模块：日线选股 + 分钟级研判，输出操作建议（文字结论 + 量化评分），**永不下单**。这是平台里唯一不允许任何下单的引擎，权限隔离的核心防线。

## 2. 职责

1. **日线选股模型**：多因子打分、估值分析、板块轮动、个股评级、支撑压力位。
2. **分钟级研判模型**：量价结构、均线趋势、背离指标、日内强弱，实时输出操作建议。
3. **标准化输出**：建议存 PG `astock_analysis` 表，供 Web 看板展示。
4. **LLM 增强分析**：把因子+信号+研报/公告喂 LLM 网关，生成自然语言研判。
5. **物理禁下单**：复用策略框架的 `XTPAdapter（2026-08-03 废止 AStockReadonlyAdapter，A股走 XTPAdapter）`，`send_order` 永久 raise。

## 3. 边界与非目标

- **不下单、不撤单、不查询持仓可交易**：只读。
- **不替代人工决策**：只输出建议，最终买卖由人决定。
- **非目标**：不做自动盯盘下单（那是 T+0 引擎的事，且 A股 T+1）。

## 4. 依赖

- 策略框架（02）：Strategy 基类、Factor、`XTPAdapter（2026-08-03 废止 AStockReadonlyAdapter，A股走 XTPAdapter）`
- 数据中台（06）：A 股日线/分钟线、基本面、复权
- LLM 网关（01）：研报/公告理解、研判生成
- 调度层（09）：盘后选股定时触发、盘中分钟级研判
- 告警（10）：关键建议/异常推送

## 5. 接口

### 5.1 模型接口
```python
class AStockAnalysisStrategy(Strategy):
    adapter = XTPAdapter（2026-08-03 废止 AStockReadonlyAdapter，A股走 XTPAdapter）()         # 受 astock 分项开关控制
    def on_bar(self, bar: Bar): ...           # 分钟级研判
    def on_daily_close(self, daily: Bar): ... # 日线选股
    def output(self) -> AnalysisResult: ...   # 输出建议
```

### 5.2 输出结构（存 PG `astock_analysis`）
```json
{
  "ts": "2026-07-22T15:00:00+08:00",
  "symbol": "SH.603986",
  "model": "daily_select_v1",
  "score": 0.78,                      // 量化评分 0-1
  "rating": "BUY",                    // BUY/HOLD/AVOID
  "factors": {"ma_dev": 0.12, "valuation": 0.34, "momentum": 0.32},
  "support": 18.50, "resistance": 19.80,
  "conclusion": "量价配合，均线多头，估值偏低……",
  "llm_summary": "（LLM 生成的自然语言研判）"
}
```

### 5.3 Web API（详见 08）
```
GET /api/astock/selection?date=YYYY-MM-DD     # 当日选股结果
GET /api/astock/analysis?symbol=SH.603xxx     # 个股研判历史
WS  /ws/astock/realtime?symbol=SH.603xxx      # 分钟级实时推送
```

## 6. 因子（复用 02 注册制，A股 category）

| 类别 | 因子示例 |
|---|---|
| trend | ma_dev, macd_divergence, adx |
| momentum | rsi, momentum_20d, volume_ratio |
| valuation | pe_pct, pb_pct, dividend_yield |
| fundamental | roe, revenue_growth（来自数据中台基本面）|
| structure | support_resistance（支撑压力位计算）|

分钟级研判复用 trend/momentum 类因子，频率切到 1min/5min。

## 7. 数据流

```
调度层(盘后) ─> 日线选股模型 ─> 数据中台取日线+基本面 ─> 因子计算 ─> 评分 ─> 存 PG
调度层(盘中) ─> 分钟级模型 ─> 数据中台实时订阅 ─> 因子 ─> 信号 ─> 存 PG + WS推送Web
研报/公告 ─> LLM网关.chat(complex) ─> 自然语言研判 ─> 并入 AnalysisResult.llm_summary
（任何环节都不调 send_order，adapter raise 兜底）
```

## 8. 配置

Web 端配置每个分析模型：标的池（全市场/自定义板块/单票）、因子组合+权重、评分阈值、LLM 是否参与。复用 02 的 `strategy_config` schema，`type="astock_analysis"`。

## 9. 权限隔离三重保障

1. **代码层**：`AStockAnalysisStrategy` 不实现 `place_order`，基类调 adapter。
2. **Adapter 层**：`XTPAdapter（2026-08-03 废止 AStockReadonlyAdapter，A股走 XTPAdapter）.send_order` 永久 `raise PermissionError`。
3. **AI 层**：LLM 网关工具白名单不含下单工具，研判只产出文字不产出指令。

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 复用策略框架 | 是，因子/信号/基类复用 | 不重复造轮子，差异只在 adapter 和输出 |
| 输出形态 | 结构化 + LLM 自然语言双轨 | 量化评分供筛选用，LLM 摘要供人读 |
| 分钟级推送 | WebSocket 推 Web | 实时研判看板 |
| 下单能力 | 物理禁用 | A 股只读是平台核心差异化，不可有触发可能 |
