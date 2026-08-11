# 模块契约 · strategy_framework（策略框架）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件。
> 配套：`docs/architecture/接口契约.md`（Signal/StrategyConfig/Order/Position/BacktestResult 结构）。

## 职责
统一策略基类 + 因子注册制 + 信号聚合 + 执行适配器 + **自建回测引擎**（纯 Python，不依赖 vnpy CtaBacktestingEngine）。
所有策略（A股分析/可转债ETF/加密合约）共用此基类，差异下沉到 `ExecutionAdapter`。
**回测/实盘 on_bar 逻辑一致**（DSL 编译成 Python 执行，回测可视化借鉴 PTrade）。

## 文件结构
```
server/src/strategy_framework/
├── strategy.py     # Strategy 基类 + Signal/Action/SignalAggregator + StrategyConfig
├── factor.py       # Factor 基类 + BarContext + 注册制 + DSL 安全 eval + 品类兼容
├── adapters.py     # ExecutionAdapter + Order/Position + XTP/CryptoPerp/Backtest Adapter + 工厂
└── backtest.py     # BacktestEngine + BacktestResult/Trade + 防未来函数 + 数据预检
```

---

## 一、public API（稳定）

### strategy.py
```python
class Strategy:
    __init__(config: StrategyConfig, adapter: ExecutionAdapter)
    on_bar(bar: dict, history: list[dict] | None = None) -> Signal | None
        # 收 K 线 -> compute_factors -> 信号聚合 -> place_order（前置风控）
        # bar: {ts, open, high, low, close, volume, ...}
        # history: 过去 bar 列表（回测引擎在 on_bar 后 append，因子只见过去）
    on_tick(tick: dict) -> None              # 实时占位（C2 实盘驱动实现）
    compute_factors(ctx: BarContext) -> dict[str, float]
    place_order(sig: Signal) -> None
        # 前置 RiskControl.check_order(order, "") -> 通过则 adapter.send_order(Order(...))
        # ⚠️ 回测时 monkey-patch 跳风控（BacktestEngine.run 内）
    @classmethod
    from_config(config: StrategyConfig, adapter) -> Strategy

@dataclass Signal: action: Action / score / symbol / volume / price / reason
class Action(Enum): BUY=1 / SELL=2 / HOLD=0
@dataclass StrategyConfig:
    id / name / type / symbol / adapter / enabled / factors: list[dict]
    aggregator: dict / risk: dict / params: dict
    # factors: [{"name":"ma_dev","weight":0.6,"params":{}}, ...]
    # type: astock_analysis / convertible_t0 / crypto_perp
    # adapter: xtp / binance_perp / okx_perp
@dataclass SignalAggregator: weights / threshold_buy=0.3 / threshold_sell=-0.3 / method
    .aggregate(factor_values: dict[str, float]) -> Signal
```
> ⚠️ `ActionSignal`（volume_type/price_type/order_validity）是**待扩展**（B6 策略表单），当前是 `Signal`。

### factor.py
```python
class BarContext:
    close / high / low / open_ / volume: float
    _history: list[dict]            # 过去 bar（无 future！防未来函数）
    sma(n: int) -> float            # 最近 n 根收盘均值（含当前）
    history: list[dict]             # read-only 属性

class Factor:
    name: str = ""
    params: dict
    def compute(self, ctx: BarContext) -> float

@register_factor(name, *, category="custom", **params)   # 装饰器自注册
list_factors(category: str | None = None) -> list[dict]
get_factor(name: str) -> dict | None

class DSLFactor(Factor):
    __init__(name: str, expr: str)
    compute(ctx) -> float           # AST 白名单 eval（_safe_eval）

validate_strategy_factors(vt_symbol: str, factor_configs: list[dict]) -> dict
    # 品类兼容校验（防 crypto 因子跑 A 股）
    # 返回 {valid, category, compatible, incompatible, message}
detect_category(vt_symbol: str) -> str   # astock/convertible/etf/crypto/unknown
```
- **预置因子**：`ma_dev`(trend) / `rsi`(trend) / `volume_ratio`(trend) / `double_low`(convertible) / `funding_rate`(crypto) / `dsl`(custom)

### adapters.py
```python
@dataclass Order: symbol / action(BUY|SELL) / volume / price / order_type(limit|market) / client_id
@dataclass Position: symbol / volume / avg_price / pnl

class ExecutionAdapter(ABC):
    @abstractmethod send_order(order: Order) -> str       # 返回 order_id（client_id）
    @abstractmethod cancel_order(order_id: str) -> None
    @abstractmethod query_position() -> list[Position]
    # 以下默认空，子类按需 override：
    query_account() -> list                # XTPAdapter 实现
    query_orders() -> list                 # 事件缓存（XTPAdapter）
    query_trades() -> list                 # 事件缓存

class XTPAdapter(ExecutionAdapter)
    # vnpy_xtp.XtpGateway 底层；事件驱动查询（调 query 后轮询等 EVENT_POSITION/ACCOUNT）
    # 品种：可转债/ETF/A股股票（中泰 XTP 通道），受三级开关
    # ⚠️ PI3 待改：__init__ 用 Broker.get_credentials()（当前 .env）
class CryptoPerpAdapter(ExecutionAdapter)   # 币安/OKX 基类
class BacktestAdapter(ExecutionAdapter)     # 回测：按当前 bar 收盘价成交，记录 Trade
    set_bar(bar) / set_commission(rate) / trades: list[Trade]

create_adapter(adapter_type: str, gateway=None, event_engine=None) -> ExecutionAdapter
    # adapter_type: xtp / binance_perp / okx_perp
```

### backtest.py
```python
@dataclass Trade: ts / symbol / action / volume / price / commission
@dataclass BacktestResult:
    start_date / end_date / initial_capital / final_value / total_return_pct
    win_rate / max_drawdown_pct / sharpe_ratio(年化,252,无风险2%) / total_trades
    daily_values: list  # [{ts, cash, position, close, value}, ...]
    trades: list        # [Trade dict, ...]
    metrics: dict       # 全部指标

class BacktestEngine:
    __init__(initial_capital=1_000_000, commission_rate=0.0005, slippage=0.0)
    run(config: StrategyConfig, bars: list[dict], shares_per_trade=100) -> BacktestResult
        # bars: [{ts, open, high, low, close, volume, ...}, ...]
        # 内部：precheck_backtest_data + validate_no_future_data + monkey-patch place_order（回测跳风控）
        # ⚠️ B1 待加：on_bar_callback 参数（每 bar 后回调，推 progress 到 Valkey）

validate_no_future_data(strategy: Strategy) -> dict
    # {valid, checks, warnings}（BarContext 无 future 属性断言）
precheck_backtest_data(config: StrategyConfig, bars: list[dict]) -> dict
    # {valid, issues, checks}（数据量/时序连续/价量为0/品类兼容）
```

---

## 二、内部 API（不保证稳定）

- `strategy._init_factors(factor_configs)` - 从配置初始化因子（DSL 或注册表）
- `factor._FACTOR_REGISTRY` / `_safe_eval(expr, ctx)` / `_DT_OPS` / `_DT_FUNCS`（AST 白名单）
- `factor.CATEGORY_COMPAT` / `FACTOR_EXCLUSIVE` / `filter_factors_by_category`
- `adapters._vnpy_exchange(ex)` / `XTPAdapter._on_order/_on_trade/_on_position/_on_account`（事件回调）/ `_wait_update(cache, before, timeout)` / `parse_vt_symbol(vt)`
- `backtest.BacktestAdapter.set_bar/set_commission` / `BacktestEngine._calculate(...)`

---

## 三、依赖

| 依赖 | 用途 |
|---|---|
| `risk_control.risk.RiskControl` | `place_order` 前置 `check_order`（延迟 import） |
| `data_platform.schema` | `to_vt_symbol`（adapters） |
| `data_platform` | `get_bars`（回测取数，经 platform 或直接 db） |
| 外部 | `vnpy`（XTPAdapter 延迟 import）/ `numpy` / `pandas` |

---

## 四、被谁调用

| 调用方 | 调什么 |
|---|---|
| `web_api.main` | 策略端点（strategy_config CRUD，待 B6 完整）+ 回测端点（待 B3） |
| `scheduler.tasks` / `strategy_runner`（C2） | `Strategy.on_bar`（实盘 XtpGateway 驱动）+ `create_adapter("xtp", ...)` |
| `astock_analysis` | `DailySelectionEngine`（用因子层） |
| `backtest` | `web_api`/`scheduler` 调 `BacktestEngine.run`（B3） |

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `strategy_config` | `web_api`（策略 CRUD） | `scheduler`（策略级开关 enabled+backtest_verified） |
| `signal_log` | 策略 place_order（待 B8/对账完善） | `risk_control.reconcile_three_books` |
| `order_log` / `trade_log` | `ExecutionAdapter.send_order`（待实盘后） | 对账 |
| `account_snapshot` | `risk.update_account_snapshot`（策略引擎调） | `risk._get_global_state` |

---

## 六、不变量

- **防未来函数**（铁律）：`BarContext` 只有当前 bar + `_history`（过去），**无 future 属性**；`validate_no_future_data` 断言；回测引擎 `history.append(bar)` 在 `on_bar` 之后
- **on_bar 签名**：`on_bar(bar: dict, history: list[dict] | None)` --回测/实盘一致
- **DSL 白名单**：`_DT_OPS`/`_DT_FUNCS`（算术 + abs/max/min/sum/round/float/int），禁 import/任意调用；表达式 ≤500 字符
- **品类兼容**：crypto 因子不能跑 A 股（`validate_strategy_factors`，`FACTOR_EXCLUSIVE`）
- **回测跳风控**：`BacktestEngine.run` monkey-patch `Strategy.place_order`（回测不调 RiskControl）
- **事件驱动查询**：`XTPAdapter` 调 `query_position/account` 后轮询等 EVENT（vnpy 4.0 异步）；`query_orders/trades` 纯事件缓存
- **三级开关**（实盘 AND）：`check_order` 前置 `.env` 总闸 + Web 分项 + 策略 enabled+backtest_verified

---

## 七、扩展指南

### 加新因子
1. `@register_factor("name", category="trend", params={"n":20})` 装饰 `Factor` 子类
2. 实现 `compute(ctx) -> float`
3. 自动进 `list_factors`（Web 可选）；品类兼容自动校验

### 加新策略
1. 继承 `Strategy`，override `on_bar`（或配置驱动 DSL，不改代码）
2. `strategy_config` DB 配（factors/aggregator/params）
3. `Strategy.from_config(config, create_adapter(...))` 实例化

### 加新执行适配器
1. 继承 `ExecutionAdapter`，实现 `send_order`/`cancel_order`/`query_position`
2. `create_adapter` mapping 注册
3. PI3：`XTPAdapter.__init__` 用 `Broker.get_credentials()`（替代 .env）

### 回测绩效扩展（B5）
- `BacktestEngine._calculate` 加 α/β/索提诺/信息率/波动率/基准收益
- `BacktestResult.metrics` 扩展字段

---

## 修订记录
- 2026-08-09 初版（基于 strategy/factor/adapters/backtest 全读核实）
