# 02 - 策略/模型统一框架

> **平台化集成（2026-08-08）**：Broker 接口（PT5）+ Task 统一任务（PT1）。XTPAdapter/Binance/OKXBroker 实现。详见记忆 `platform-architecture`。
>
> **因子平台化 + 双模式 + 任务分离（2026-08-11）**：① 因子 DB 化（`factor_def` 表，用户 Web 写 Python 自定义因子）+ 静态/动态区分（`needs_history`）；② 双模式策略执行（DSL + Python 代码框 `PythonStrategy`）；③ 参数定义系统（`parameter_defs`，任务动态表单）；④ 策略与实盘/回测任务分离（`live_task` 表，一标的一进程，策略快照隔离）。
>
> **运行时重构（2026-08-25~27，批 1/2/4）**：SDK 生命周期守卫（`md_api_guard.py`）+ 共享引擎骨架（`runtime/`）+ 交易域单源化（`strategy_runner/trading.py` 九单元）——三引擎复制主循环与 XTP SDK 裸调的结构性根治，**见 §11**。设计背景：12 号 §2.9/2.10；落地状态：`flow/待办.md` 重构表。

## 1. 目的

提供跨三市场统一的策略/模型抽象：**统一的是模型结构，不是执行语义**。可转债 T+0、A 股只读分析、加密永续合约共用一套 `Strategy` 基类 + `Factor` 注册制 + `SignalAggregator` + `ExecutionAdapter` 抽象，差异下沉到适配器。

让策略可**配置驱动**（Web 端选因子、调权重、改参数、写 DSL 表达式 / Python 代码），而不是每个模型改代码硬编码。

## 2. 职责

1. **统一 Strategy 基类**：`on_bar` / `on_tick` 回调、持仓管理、下单委托。
2. **Factor 组件 + 注册制**：因子统一接口，启动自注册预置因子 + DB 加载自定义因子。
3. **因子静态/动态区分**：`needs_history=0` 静态（可选股+策略），`>0` 动态（只能策略）。
4. **信号聚合**：因子值 + 权重 → 买/卖/持信号。
5. **DSL 表达式引擎 + Python 代码模式**：受限 DSL 表达式 + 用户写 `on_bar(ctx)` 的 Python 代码框（双模式统一执行）。
6. **参数定义系统**：策略声明 `parameter_defs`（参数 schema），创建任务时动态生成表单。
7. **ExecutionAdapter 抽象**：A 股只读（raise）、场内 XTP、加密币安/OKX 三种实现。
8. **配置 schema + Web 配置 → 运行实例**：JSON 配置实例化策略，参数热改。
9. **回测/实盘同构**：策略代码不变，切换 `backtest` / `live` 模式只换数据源与 adapter。
10. **策略与任务分离**：`strategy_config` 为配方（不绑标的），`live_task` 为实盘任务（绑定策略+标的+参数值），`backtest_runs` 为回测任务（多标的+per-symbol 参数）。

## 3. 边界与非目标

- **不做：回测引擎已自建 BacktestEngine（纯 Python，不依赖 vnpy CtaBacktestingEngine）、交易网关（用 vnpy 网关）。
- **非目标**：不做图形化拖拽策略编辑器（Web 表单 + DSL/Python 代码框即可）。

## 4. 依赖

- vnpy `BaseStrategy` / `CtaTemplate`（策略基类参考，不强制继承，可适配）
- 数据中台（行情）
- 风控中心（下单前置校验）

## 5. 核心抽象

### 5.1 Strategy 基类 + PythonStrategy
```python
class Strategy:
    id: str                        # 实例ID
    symbol: str                    # 标的（实盘由 live_task 提供，回测由 backtest_runs 提供）
    config: StrategyConfig         # 配置：因子列表+权重+参数+parameter_defs
    adapter: ExecutionAdapter

    def on_bar(self, bar: Bar, history=None) -> Signal | None:
        ctx = BarContext(close, high, low, open_, volume, history)
        fv = self.compute_factors(ctx)
        sig = self._aggregator.aggregate(fv)
        if sig.action != HOLD:
            self.place_order(sig)   # 前置风控 check_order → adapter.send_order

    @classmethod
    def from_config(cls, config, adapter):
        if config.params.get("mode") == "python":
            return PythonStrategy(config, adapter)   # 双模式：Python 代码框
        return _STRATEGY_REGISTRY.get(config.type, cls)(config, adapter)

class PythonStrategy(Strategy):
    """Python 代码模式（#15）：用户写 on_bar(ctx) 函数，受限 namespace exec。"""
    def on_bar(self, bar, history=None):
        self._ctx._update(bar, history, self.config.params)
        safe_builtins = {abs, max, min, sum, round, int, float, len, range, ...}
        namespace = {"ctx": self._ctx, "on_bar": None, "__builtins__": safe_builtins}
        exec(self._compiled, namespace)
        sig = namespace["on_bar"](self._ctx)
        if sig and sig.action != HOLD:
            self.place_order(sig)
```

**StrategyContext**（Python 模式用户唯一可用 API）：`get_bar()` / `get_history(n)` / `get_full_history(n)` / `get_param(key)` / `get_factor(name, **kw)` / `buy()` / `sell()` / `hold()` / `set_state()` / `get_state()`。

### 5.2 Factor 接口 + 注册制（预置 + 自定义 DB 因子）
```python
@register_factor("ma_dev", category="trend", params={"n": 20}, needs_history=20)  # 动态因子
class MADevFactor(Factor):
    def compute(self, ctx: BarContext) -> float:
        return ctx.close / ctx.sma(self.params["n"]) - 1

@register_factor("double_low", category="convertible", needs_history=0)  # 静态因子
class DoubleLowFactor(Factor): ...
```
- **预置因子**：`@register_factor` 装饰器注册到 `_FACTOR_REGISTRY`
- **自定义因子**：用户 Web 写 Python 代码（`compute(ctx, **params)` 函数），存 `factor_def` 表，`_make_factor_class()` 编译为 Factor 子类，`load_factors_from_db()` 启动加载
- **静态/动态**：`needs_history` 字段，0=静态（可选股+策略），>0=动态（只能策略）。`list_factors(static_only=True)` 供选股引擎过滤
- **安全**：自定义因子代码走 `_check_ast_blacklist`（禁 import/exec/eval/open）+ 受限 `__builtins__` namespace

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
聚合方式可插拔（加权求和 / 投票 / 阈值），但默认加权求和覆盖 80% 场景。`Signal` 含 ActionSignal 扩展（`volume_type`/`price_type`/`order_validity`）。

### 5.4 DSL 表达式引擎（受限）+ Python 代码框（双模式）
```python
# 模式一：DSL 表达式（受限 eval，AST 白名单）
expr = "ma_dev * 0.6 + rsi * 0.4"      # 策略级 DSL
factor = DSLFactor("my_dev", expr=expr) # 安全 eval，白名单函数

# 模式二：Python 代码框（PythonStrategy，受限 namespace exec）
def on_bar(ctx):
    close = ctx.get_bar("close")
    hist = ctx.get_history(20)
    if len(hist) >= 20:
        sma = sum(hist) / len(hist)
        if close > sma * 1.02: return ctx.buy(100)
    return ctx.hold()
```
**安全措施**：DSL 走 AST 白名单（算术+索引+白名单函数）；Python 代码框走 `_check_ast_blacklist`（禁 import/exec/eval/open/__import__）+ 受限 `__builtins__` + systemd 子进程隔离。

### 5.5 参数定义系统（parameter_defs）
```python
# 策略声明参数 schema（存 strategy_config.params["parameter_defs"]）
parameter_defs = [
    {"name": "buy_threshold", "type": "number", "default": 0.02, "min": 0, "max": 1, "label": "买入阈值"},
    {"name": "use_trailing_stop", "type": "boolean", "default": False},
]

# 创建任务时：前端读 parameter_defs 动态生成表单 → 用户填值 → 存 live_task.params
# Python 策略代码读参数：threshold = ctx.get_param("buy_threshold")
```
校验函数：`validate_parameter_defs()` 校验 schema 结构，`validate_params_against_defs()` 校验参数值类型/范围，`build_default_params()` 构建默认值。

### 5.6 ExecutionAdapter
```python
class ExecutionAdapter:
    def send_order(self, order: Order) -> str: ...      # 返回 order_id
    def cancel_order(self, order_id: str) -> None: ...
    def query_position(self) -> list[Position]: ...

class XTPAdapter(ExecutionAdapter):       # vnpy_xtp 网关
class BinancePerpAdapter(ExecutionAdapter): ...   # vnpy 加密网关
class OKXPerpAdapter(ExecutionAdapter): ...
```

## 6. 配置 schema（策略与任务分离）

### strategy_config（策略配方，不绑标的）
```json
{
  "id": "ma_trend",
  "name": "均线趋势策略",
  "type": "astock_analysis",
  "adapter": "xtp",
  "factors": [{"name": "ma_dev", "weight": 1.0, "params": {"n": 20}}],
  "aggregator": {"method": "weighted_sum", "threshold_buy": 0.3, "threshold_sell": -0.3},
  "risk": {"stop_loss_pct": 0.03},
  "params": {
    "mode": "python",
    "python_code": "def on_bar(ctx):\n    ...",
    "parameter_defs": [{"name": "buy_threshold", "type": "number", "default": 0.02, "min": 0, "max": 1}]
  },
  "enabled": true,
  "backtest_verified": true
}
```

### live_task（实盘任务，一标的一进程）
```json
{
  "id": 1, "name": "茅台均线",
  "strategy_id": "ma_trend", "symbol": "600000.SHSE",
  "params": {"buy_threshold": 0.03},          // 任务级参数值（覆盖策略默认）
  "strategy_snapshot": {...},                  // 创建时策略快照（隔离，改策略不影响已跑任务）
  "status": "running", "task_id": 42, "systemd_unit": "quant-live-task@1",
  "account_id": "253191001822", "initial_capital": 1000000
}
```

### backtest_runs（回测任务，多标的 + per-symbol 参数）
```json
{
  "id": 42, "strategy_config_id": "ma_trend",
  "symbols": ["600000.SHSE", "600001.SHSE"],
  "params": {"buy_threshold": 0.02, "capital": 1000000, "start": "2025-01-01", "end": "2026-01-01"},
  "symbol_params": {                           // per-symbol 覆盖
    "600000.SHSE": {"buy_threshold": 0.03},
    "600001.SHSE": {"buy_threshold": 0.02}
  },
  "mode": "parallel", "status": "running"
}
```

## 7. 热更新

| 类型 | 机制 | 重启? |
|---|---|---|
| 参数热改（权重/阈值/风控参数） | 监听配置变更 → 策略对象 reload 配置 | 否 |
| DSL 表达式改动 | 重新 eval | 否 |
| Python 代码改动 | 属发版，重启策略进程 | 是 |
| 实盘任务参数 | 改 live_task.params 后重启该任务进程 | 是（单进程） |

**策略快照隔离**：`live_task` 创建时固化 `strategy_snapshot`，后续改 `strategy_config` 不影响已运行的实盘任务。

## 8. 回测 / 实盘同构

```python
# 回测（自建 BacktestEngine，纯 Python）
engine = BacktestEngine(initial_capital=1_000_000, commission_rate=0.0005)
# 逐标的：合并 策略默认 params + per-symbol 覆盖
for symbol in symbols:
    merged = {**strategy_params, **symbol_params.get(symbol, {})}
    cfg = StrategyConfig(..., symbol=symbol, params=merged)
    result = engine.run(cfg, bars, on_bar_callback=callback)

# 实盘（strategy_runner --task-id <live_task_id>）
# 读 live_task + strategy_snapshot → StrategyConfig → Strategy.from_config
# 订阅 live_task.symbol → tick → on_bar → place_order
```
**策略代码和配置完全不变**，回测/实盘同构，零迁移切 live。

## 9. 与其它模块交互

- **数据中台**：`get_bar` / `subscribe` 喂 `on_bar/on_tick`。
- **风控中心**：`place_order` 前置 `check_order`；`emergency_halt` 后策略停开新仓。
- **LLM 网关**：A股分析模型把因子+信号+研报喂 LLM 生成建议（不下单）。
- **Web 管理后台**：策略配置 CRUD + 实盘任务 CRUD（`/api/live-task`）+ 回测（`/api/backtest`）。
- **调度层**：定时选股策略（只用静态因子）、盘后调仓触发。
- **告警**：策略异常/熔断触发推送。

## 10. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 自建基类 vs 强制继承 vnpy CtaTemplate | 自建基类 + 可适配 vnpy | A 股分析模型非交易策略，形态不同，统一基类更清晰 |
| 因子组合方式 | 加权求和默认，可插拔 | 覆盖 80%，复杂场景留扩展 |
| 自定义因子 | DSL 表达式 + DB 化 Python 因子 | 安全 + 覆盖量化的因子公式 + 复杂逻辑用 Python |
| 因子静态/动态 | `needs_history` 区分 | 静态可选股（扫全市场便宜），动态只能策略（需历史窗口） |
| 策略执行模式 | DSL + Python 代码框双模式 | DSL 覆盖 80% 公式，Python 代码框覆盖 ML/复杂状态机 |
| 策略与标的 | 分离（strategy_config 不绑标的） | 策略可复用，一策略多任务，改策略不影响已跑任务（快照隔离） |
| 实盘执行模型 | 一标的一进程（live_task） | 实盘重稳定，独立重启互不影响，资源不够加机器 |
| 参数系统 | parameter_defs 声明 + 任务填值 | 自文档化，前端动态表单，校验自动化 |
| 任意 Python | 受限 namespace + AST 校验 + systemd 隔离 | 安全风险可控，DSL/Python 代码框覆盖大部分需求 |

## 11. 运行时骨架与 SDK 守卫（2026-08-25 一日三事故驱动，批 1/2/4 落地）

> 背景：SEGV 崩溃循环（setHeartBeatInterval 在 createQuoteApi 之前调 C）+ 反应式重登死路（三份复制主循环漂移）+ XTP 半开陷阱——详见 12 号 §2.9。三个组件均已部署生产。

### 11.1 SDK 生命周期守卫 `md_api_guard.py`（批 1）

**GuardedXtpMdApi（XtpMdApi 守卫子类）四态状态机**：

```
IDLE ──createQuoteApi──> CREATED ──login──> LOGGED_IN
（C 对象未建，任何       （已建未登录）        │ logout/onDisconnected
 C 方法调用都可能 SEGV）                       ▼
                                            CREATED（可重登）
DEAD ←──────────────── close() ───────────────┘（此后 relogin/login 必拒）
```

- **官方时序只在 `connect()` 内发生**：createQuoteApi→setHeartBeatInterval→login，非法时刻调用抛 `SdkLifecycleError`（Python 异常），**永不到 C 层**——SEGV 类事故结构性绝迹
- **有意不含 RELOGGING 态**：quote login 同步返回，logout→login 在 `relogin()` 一次调用内完成，中间态对外不可观测
- 线程模型：引擎面（connect/relogin）与 SDK 回调面（onDisconnected→login_server）全程 RLock 互斥；`login_server` 永不抛；`subscribe` 软防护（非 LOGGED_IN 态 no-op，供周期幂等重放）
- 官方语义出处：`docs/reference/xtp-sdks/` header 注释（Login -2 = 须先 logout；SetHeartBeatInterval 必须在 Login 之前）

### 11.2 共享引擎骨架 `runtime/`（批 2，hub 首迁；批 4，worker 迁）

单点化三引擎（hub/runner/worker）复制主循环的公共职责，**三引擎退化为声明式钩子**：

| 模块 | 职责 |
|---|---|
| `loop.py` EngineLoop | **到期驱动**钩子调度（废 counter%N 相位耦合——hub 10s/5s flush 窗口历史坑的结构性根治） |
| `mdlink.py` MdSessionSupervisor | L2 会话自愈收编（定时续航/反应式重登/退避，12 号 §2.8 硬规则 2 的实现位） |
| `pulse.py` | 心跳（Valkey HASH）+ 看门狗喂狗 |
| `subs.py` SubscriptionManager | 订阅幂等重放（60s 周期 diff） |
| `alerts.py` AlertPolicy | 告警策略（去重/级别） |
| `xsleeper.py` | 阻塞读休眠（XReadSleeper——Redis BLOCK 0 永久阻塞的结构性防御，批 4b 双盲 P1 产物） |

### 11.3 交易域单源化 `strategy_runner/trading.py`（批 4a，九单元）

下单时刻安全判定/持仓快照/冻结语义/对账等九个交易域单元从 main.py/hub_worker.py 提取为单源（双盲 AST 级 diff 证九单元与原实现逐字一致）：`buy_ok_check`（下单时刻新鲜度判定，15 号平面 D）/ `frozen_allows`（sticky 冻结，SELL 放行）/ `write_trade_log` / `snapshot_cycle`（持仓真相源 60s 覆盖）/ `halt_edge_cancel`（熔断沿撤单）/ `recalc_hook` / `stop_due` / `reconcile_orders`（启动对账，12 号 §2.1）/ `_flush_positions`。

**direct 模式冻结**（批 4）：修复照做、迁移不做——所有新工作落 hub 模式，direct 退役走批 6 收口。

> 细节与签名：模块契约 `strategy_framework.md`（runtime/guard）+ `strategy_runner.md`（trading.py 九单元/direct 冻结语义）。
