# 02 - 策略/模型统一框架

## 1. 目的

提供跨三市场统一的策略/模型抽象：**统一的是模型结构，不是执行语义**。可转债 T+0、A 股只读分析、加密永续合约共用一套 `Strategy` 基类 + `Factor` 注册制 + `SignalAggregator` + `ExecutionAdapter` 抽象，差异下沉到适配器。

让策略可**配置驱动**（Web 端选因子、调权重、改参数、写 DSL 表达式），而不是每个模型改代码硬编码。

## 2. 职责

1. **统一 Strategy 基类**：`on_bar` / `on_tick` 回调、持仓管理、下单委托。
2. **Factor 组件 + 注册制**：因子统一接口，启动自注册，Web 可选。
3. **信号聚合**：因子值 + 权重 → 买/卖/持信号。
4. **DSL 表达式引擎**：受限表达式定义自定义因子，安全 eval。
5. **ExecutionAdapter 抽象**：A 股只读（raise）、场内 XTP、加密币安/OKX 三种实现。
6. **配置 schema + Web 配置 → 运行实例**：JSON 配置实例化策略，参数热改，代码热重载。
7. **回测/实盘同构**：策略代码不变，切换 `backtest` / `live` 模式只换数据源与 adapter。

## 3. 边界与非目标

- **不做**：回测引擎本身（用 VeighNa CtaBacktestingEngine）、交易网关（用 vnpy 网关）。
- **不早期做**：Web 端写任意 Python + 沙箱（留作最后 5%）。
- **非目标**：不做图形化拖拽策略编辑器（Web 表单 + DSL 即可）。

## 4. 依赖

- vnpy `BaseStrategy` / `CtaTemplate`（策略基类参考，不强制继承，可适配）
- VeighNa 回测引擎（回测模式接入）
- 数据中台（行情）
- 风控中心（下单前置校验）

## 5. 核心抽象

### 5.1 Strategy 基类
```python
class Strategy:
    id: str                        # 实例ID（一个标的一个实例）
    symbol: str                    # 标的，如 SH.603xxx / 113xxx / BTCUSDT-PERP
    config: StrategyConfig         # Web 配置：因子列表+权重+参数
    adapter: ExecutionAdapter      # 执行适配器（A股只读/XTP/加密）

    def on_bar(self, bar: Bar) -> None:
        fv = self.compute_factors(bar)
        sig = self.aggregate(fv)
        if sig.action != HOLD:
            self.place_order(sig)

    def on_tick(self, tick: Tick) -> None: ...
    def compute_factors(self, bar: Bar) -> dict[str, float]: ...
    def aggregate(self, factor_values: dict) -> Signal: ...
    def place_order(self, sig: Signal) -> None:
        order = self._build_order(sig)
        RiskControl.get().check_order(order, self.account)   # 前置风控
        self.adapter.send_order(order)                        # A股 raise
```

### 5.2 Factor 接口 + 注册制
```python
@register_factor("ma_dev", category="trend", params={"n": 20})
class MADevFactor(Factor):
    def compute(self, ctx: BarContext) -> float:
        return ctx.close / ctx.sma(ctx.close, self.params["n"]) - 1

@register_factor("double_low", category="convertible")  # 可转债双低
class DoubleLowFactor(Factor): ...

@register_factor("funding_rate", category="crypto")     # 资金费率
class FundingRateFactor(Factor): ...
```
启动时扫描所有 `@register_factor` → 因子注册表 → Web 端读"可选因子清单"。因子按 category 分组（trend/meanrev/convertible/crypto/fundamental），Web 按策略类型过滤可见因子。

### 5.3 SignalAggregator
```python
class SignalAggregator:
    weights: dict[str, float]      # 来自配置
    threshold_buy: float
    threshold_sell: float
    def aggregate(self, factor_values: dict[str, float]) -> Signal:
        score = sum(factor_values[k] * self.weights[k] for k in factor_values)
        action = BUY if score > self.threshold_buy else SELL if score < -self.threshold_sell else HOLD
        return Signal(action=action, score=score)
```
聚合方式可插拔（加权求和 / 投票 / 阈值），但默认加权求和覆盖 80% 场景。

### 5.4 DSL 表达式引擎（受限）
```python
# Web 端可写自定义因子表达式（不写 Python 代码）
expr = "mean(close, 20) / close - 1"      # 偏离均线
factor = DSLFactor("my_dev", expr=expr)    # 安全 eval，白名单函数
```
**安全措施**：AST 白名单（只允许算术+索引+已注册函数 `mean/sum/std/max/min/rank/zscore`）；禁止 import/attribute/call 任意对象；超时 + 长度限制；`eval` 在受限 namespace。

### 5.5 ExecutionAdapter
```python
class ExecutionAdapter:
    def send_order(self, order: Order) -> str: ...      # 返回 order_id
    def cancel_order(self, order_id: str) -> None: ...
    def query_position(self) -> list[Position]: ...

class AStockReadonlyAdapter(ExecutionAdapter):
    def send_order(self, order): raise PermissionError("A股只读，永久禁下单")

class XTPAdapter(ExecutionAdapter):       # vnpy_xtp 网关
    def send_order(self, order): return self.gateway.send_order(...)

class BinancePerpAdapter(ExecutionAdapter): ...   # vnpy 加密网关
class OKXPerpAdapter(ExecutionAdapter): ...
```

## 6. 配置 schema（Web → DB → 运行实例）
```json
{
  "id": "conv-doublelow-01",
  "name": "可转债双低轮动",
  "type": "convertible_t0",
  "symbol": "all_convertible",          // 或具体代码
  "adapter": "xtp",
  "factors": [
    {"name": "double_low", "weight": 0.6},
    {"name": "dsl:my_premium", "weight": 0.2, "expr": "close/conv_value - 1"},
    {"name": "volume_ratio", "weight": 0.2, "params": {"n": 5}}
  ],
  "aggregator": {"method": "weighted_sum", "threshold_buy": 0.3, "threshold_sell": -0.3},
  "risk": {"stop_loss_pct": 0.03, "max_position_pct": 0.15, "max_trades_per_day": 20},
  "params": {"rebalance_days": 5},
  "enabled": true
}
```
配置存 PG `strategy_config` 表，Web 改完写库，运行进程监听变更。

## 7. 热更新（分两类）

| 类型 | 机制 | 重启? |
|---|---|---|
| 参数热改（权重/阈值/标的/风控参数） | 监听配置变更 → 策略对象 reload 配置 | 否 |
| 代码热重载（改因子逻辑/DSL 表达式） | DSL 改了重新解析；预置因子改代码需重新实例化策略对象 + 状态迁移 | 否（但要注意持仓/挂单状态迁移） |

参数热改覆盖日常调参；DSL 表达式改动立即生效（重新 eval）；预置因子代码改动属发版，建议低频、盘后做，配合状态保存/恢复。

## 8. 回测 / 实盘同构

```python
# 回测
engine = CtaBacktestingEngine()
engine.set_data(data_platform.get_bar(...))   # 历史数据
engine.add_strategy(MyStrategy, config)
engine.run_backtesting()

# 实盘
adapter = XTPAdapter(...)
engine = LiveEngine(adapter=adapter)
engine.add_strategy(MyStrategy, config)
engine.run_live()
```
**MyStrategy 代码和 config 完全不变**。数据 schema（06 数据中台定义）回测与 XTP 实时行情一致，20 天等待期回测结果零迁移切 live。

## 9. 与其它模块交互

- **数据中台**：`get_bar` / `subscribe` 喂 `on_bar/on_tick`。
- **风控中心**：`place_order` 前置 `check_order`；`emergency_halt` 后策略停开新仓。
- **LLM 网关**：A股分析模型把因子+信号+研报喂 LLM 生成建议（不下单）。
- **Web 管理后台**：读写 `strategy_config`，启停策略，改参数。
- **调度层**：定时选股策略、盘后调仓触发。
- **告警**：策略异常/熔断触发推送。

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 自建基类 vs 强制继承 vnpy CtaTemplate | 自建基类 + 可适配 vnpy | A 股分析模型非交易策略，形态不同，统一基类更清晰 |
| 因子组合方式 | 加权求和默认，可插拔 | 覆盖 80%，复杂场景留扩展 |
| 自定义因子 | DSL 表达式（受限 eval） | 安全 + 覆盖量化的因子公式 |
| 任意 Python | 不早期做（沙箱留作后路） | 安全风险大，DSL 足够覆盖 |
| A股 adapter | send_order 永久 raise | 权限隔离从抽象层就锁死 |
| 热更新 | 参数热改 + DSL 即时 + 代码盘后发版 | 平衡灵活与稳定 |
