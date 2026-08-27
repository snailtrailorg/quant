# 模块契约 · strategy_framework（策略框架）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件。
> 配套：`docs/architecture/接口契约.md`（Signal/StrategyConfig/Order/Position/BacktestResult/live_task/parameter_defs 结构）。

> **最近变更（2026-08-27 深夜）**：adapters 查询超时可观测（P2-1，db73fe0）——`_wait_update -> bool`
> /`POSITION_STABLE_WINDOW_S` 常量化/稳定窗分级告警。详见任务文件 docs/任务/P2-1-查询超时可观测.md。

## 职责
统一策略基类 + 因子注册制（预置 + DB 自定义）+ 信号聚合 + 执行适配器 + **自建回测引擎**（纯 Python）。
所有策略（A股分析/可转债ETF/加密合约）共用此基类，差异下沉到 `ExecutionAdapter`。
**双模式执行**（DSL + Python 代码框）+ **参数定义系统**（parameter_defs）+ **策略与任务分离**（strategy_config 配方 / live_task 实盘任务 / backtest_runs 回测任务）。

## 文件结构
```
server/src/strategy_framework/
├── strategy.py     # Strategy 基类 + PythonStrategy + StrategyContext + SignalAggregator + StrategyConfig + parameter_defs 校验
├── factor.py       # Factor 基类 + BarContext + 注册制 + DSL 安全 eval + 自定义因子 DB 化 + 静态/动态区分 + AST 校验
├── adapters.py     # ExecutionAdapter + Order/Position + XTP/CryptoPerp/Backtest Adapter + 工厂
├── backtest.py     # BacktestEngine + BacktestResult/Trade + 防未来函数 + 数据预检 + symbol_params
├── broker.py       # Broker ABC + XTP/Binance/OKXBroker + get_broker() + record_broker_usage() + build_xtp_setting()
├── md_api_guard.py # GuardedXtpMdApi：XTP 行情 SDK 生命周期守卫（状态机+RLock，批 1）
├── md_session.py   # MdSessionBase 契约 + XtpMdSession + is_trading_day/zombie_session（L2 会话层）
└── runtime/        # 引擎运行时骨架（批 2；hub/worker 已迁，direct 冻结批 6 退役）
    ├── loop.py     # EngineLoop + Hook：到期驱动钩子循环
    ├── pulse.py    # SessionCounters + HeartbeatWriter
    ├── mdlink.py   # MdSessionSupervisor（L2 会话监督器）
    ├── alerts.py   # AlertPolicy + make_alert/make_guard/make_valkey 三件套
    ├── subs.py     # SubscriptionManager（订阅 diff/幂等重放）
    └── xsleeper.py # XReadSleeper（worker 双节奏 sleeper，批 4b）
```

> 注：`runtime/` 拟改名 `enginekit/`（批 3c 计划未执行）——契约按现名 `strategy_framework/runtime/` 写。

---

## 一、public API（稳定）

### strategy.py
```python
class Strategy:
    __init__(config: StrategyConfig, adapter: ExecutionAdapter)
    on_bar(bar: dict, history: list[dict] | None = None) -> Signal | None
        # 收 K 线 -> compute_factors -> 信号聚合 -> place_order（前置风控）
    on_tick(tick: dict) -> None
    compute_factors(ctx: BarContext) -> dict[str, float]
    place_order(sig: Signal) -> None         # 前置 RiskControl.check_order -> adapter.send_order
    @classmethod
    from_config(config: StrategyConfig, adapter) -> Strategy
        # mode=python -> PythonStrategy；否则按 type 查 _STRATEGY_REGISTRY

class PythonStrategy(Strategy):              # #15 Python 代码模式
    # 用户写 on_bar(ctx) 函数，受限 namespace exec（safe_builtins）
    # 编译：compile(python_code, "<strategy>", "exec")
    # 执行：exec(compiled, {"ctx", "on_bar", "__builtins__": safe_builtins})

class StrategyContext:                        # Python 模式用户唯一可用 API
    get_bar(field="close", default=0) -> float
    get_history(n=20) -> list[float]          # 最近 n 根 close（不含当前）
    get_full_history(n=20) -> list[dict]
    get_param(key, default=None)              # 任务级 + 策略级参数
    get_factor(name, **kwargs) -> float       # 调注册因子（预置/自定义）
    buy(volume=100, price_type="LIMIT") -> Signal
    sell(volume=100, price_type="LIMIT") -> Signal
    hold(reason="") -> Signal
    set_state(key, value) / get_state(key, default=None)

@dataclass Signal: action: Action / score / symbol / volume / price / reason
    # ActionSignal 扩展：volume_type(SHARES/PERCENT/ALL_IN) / price_type(LIMIT/MARKET) / order_validity(DAY/GTC)
class Action(Enum): BUY=1 / SELL=2 / HOLD=0
@dataclass StrategyConfig:
    id / name / type / symbol / adapter / enabled / factors: list[dict]
    aggregator: dict / risk: dict / params: dict
    # params 含 mode(dsl/python) / python_code / dsl_expr / parameter_defs / volume_type 等
@dataclass SignalAggregator: weights / threshold_buy=0.3 / threshold_sell=-0.3 / method

# 参数定义系统（parameter_defs）
validate_parameter_defs(defs: list[dict]) -> str | None    # 校验 schema 结构
build_default_params(defs: list[dict]) -> dict             # 构建默认参数值
validate_params_against_defs(params: dict, defs: list[dict]) -> str | None  # 校验值类型/范围
```

### factor.py
```python
class BarContext:
    close / high / low / open_ / volume: float
    _history: list[dict]            # 过去 bar（无 future！防未来函数）
    sma(n: int) -> float            # 最近 n 根收盘均值（含当前，P4-1 缓存）
    history: list[dict]             # read-only 属性

class Factor:
    name: str = ""
    params: dict
    def compute(self, ctx: BarContext) -> float

@register_factor(name, *, category="custom", needs_history=0, **params)  # needs_history: 0=静态,>0=动态窗口
list_factors(category=None, static_only=False) -> list[dict]   # static_only=True 只返回静态因子
get_factor(name: str) -> dict | None                           # 返回 {cls, name, category, params, is_custom, needs_history}

# 自定义因子（DB 化）
_make_factor_class(name, code, default_params) -> type        # 编译用户 Python 代码为 Factor 子类
load_factors_from_db() -> list[str]                           # 启动从 factor_def 表加载
register_custom_factor(name, category, code, description, params, needs_history=0) -> dict  # CRUD
delete_custom_factor(name) -> bool

class DSLFactor(Factor):
    __init__(name: str, expr: str)
    compute(ctx) -> float           # AST 白名单 eval（_safe_eval）

# AST 安全校验（Python 代码框 + 自定义因子共用）
_check_ast_blacklist(code: str) -> str | None                  # 禁 import/exec/eval/open/__import__ 等
validate_strategy_factors(vt_symbol, factor_configs) -> dict   # 品类兼容校验
detect_category(vt_symbol) -> str                              # astock/convertible/etf/crypto/unknown
```
- **预置因子**：`ma_dev`(trend,needs_history=20) / `rsi`(trend,14) / `volume_ratio`(trend,5) / `double_low`(convertible,0) / `funding_rate`(crypto,0) / `dsl`(custom,0)
- **自定义因子**：用户 Web 写 `compute(ctx, **params)` 函数，存 `factor_def` 表，受限 namespace 编译

### adapters.py / backtest.py / broker.py
（同前，略）

### backtest.py 增强
```python
class BacktestEngine:
    run(config, bars, shares_per_trade=100, on_bar_callback=None) -> BacktestResult
    # 逐标的调用时由调用方合并 symbol_params（见 scheduler.tasks.backtest_symbol_task）
```

### runtime/（引擎运行时骨架——批 2 落地；hub/worker 已迁，direct 冻结批 6 退役）

> 详见 `模块契约/md_hub.md`（hub 侧接线+钩子表）与 `模块契约/strategy_runner.md`（worker 侧钩子表）。

```python
# loop.py —— 到期驱动钩子循环（废 while True + sleep + counter%N 相位耦合）
@dataclass Hook: name / period(秒，0=每步) / fn / failure("log"|"exit")
class EngineLoop:
    __init__(*, name, step=5.0, sleeper=None, now=None, watchdog=None,
                 event_engines=(), on_fatal=None, fatal_exit_code=1)
    every(name, period, fn, failure="log")   # 注册钩子；重名拒绝（防静默覆盖）
    run(stop_after_iterations=0)             # 永续：睡距最近到期(上限 step)→preflight→分发
# sleeper/now 皆可注入（假时钟确定性测试零真实等待）；preflight=喂狗+事件线程存活(死→on_fatal+os._exit)

# pulse.py —— 时段作用域计数 + 心跳
class SessionCounters:                # 事件线程写 on_data / 主循环读（GIL 原子，vnxtpmd 全程持 GIL 已实证）
    on_data(in_session)                       # tick/bar 到达
    apply_edge(in_session) -> bool            # 沿处理：进沿 True+写 enter_ts+清基线（盘中首调视为进沿）
    zombie(now=None, trading_day=True, grace=None) -> bool   # 委托 md_session.zombie_session（唯一实现，grace 透传）
    stalled(now=None) -> float | None         # 断流秒数；时段内无基线 None
class HeartbeatWriter:
    __init__(r, key, ttl=90, base=None); beat(**extra) -> None   # hset+expire；失败仅警告；ts 兜底

# mdlink.py —— L2 会话监督器
class MdSessionSupervisor:
    __init__(session, counters, alert, *, role="hub", policy: AlertPolicy=None,
                 context=None, now=None)
    tick(in_session, trading_day) -> None     # 永不抛。五段：沿→定时续航→反应式重登(症状+退避)→恢复
                                              # →例行告警(零tick/断流双通道限频)；段首 stalled 源头门
                                              # （非盘中恒 None，D1——防未来无条件喂引擎夜间误告警）

# alerts.py —— 告警策略 + 三件套工厂
@dataclass AlertPolicy: zombie_grace=600 / stall_error=300 / zero_tick_alert_period=150 /
                        stall_alert_period=30 / recover_window=60     # 默认=hub 现值（批 2 迁移行为不变）
make_alert() -> Callable[[str, str], None]   # safe_notify 包装，never-raise（告警通道故障不反噬主流程）
make_guard(name, alert)                      # quant_common.guard 包装（事件线程 handler 守卫，F-26）
make_valkey()                                # VALKEY_URL 连接，socket_timeout=3（监控件不被存储拖死）

# subs.py —— 订阅管理
class SubscriptionManager:
    __init__(desired: Callable[[], set[str]], subscribe, unsubscribe)  # 纯逻辑不持周期（节奏由引擎注册）
    current -> set[str]                       # 已同步集（拷贝；心跳 subs 计数用）
    poll()                                    # diff 增删（先加后退；期望集读失败沿用旧集）
    replay()                                  # 全量幂等重放：**先退 removed** 再全量订（防订阅泄漏）
    on_reconnect_edge()                       # 重连沿强制重放（XTP 重连不恢复订阅）

# xsleeper.py —— worker 双节奏 sleeper（批 4b）
class XReadSleeper:                           # EngineLoop.sleeper 协议实现
    __init__(r, stream, group, consumer, on_batch)
    __call__(seconds)                         # block=min(500, 距到期 ms)、钳 1ms 禁 BLOCK 0
# never-raise：边界全异常不外抛(含 on_batch)；Timeout 静默/其他吞后睡 1s 下轮再试(禁内旋)；
# NOGROUP → os._exit(75) 交 systemd 重启→组重建；单线程模型(on_batch 在 loop 线程内联，禁后台线程)
```

### md_api_guard.py（XTP 行情 SDK 生命周期守卫，批 1——2026-08-25 SEGV 事故终结防御）

```python
class SdkState(Enum): IDLE / CREATED / LOGGED_IN / DEAD
class SdkLifecycleError(RuntimeError)         # SDK 非法时刻调用——Python 拦截，永不到 C 层
class GuardedXtpMdApi(XtpMdApi):              # 四入口（状态机 IDLE→CREATED→LOGGED_IN；引擎/回调两面 RLock 互斥）
    connect(userid, password, client_id, server_ip, server_port,
            quote_protocol, log_level)        # 唯一建 C 对象+首登入口（createQuoteApi→心跳 15s→login）
    relogin() -> bool                         # 引擎面（严格态校验）：-2 官方序列（LOGGED_IN 先 logout 清场再 login）
    login_server() -> bool                    # SDK 回调线程面（父类 onDisconnected 调用）——永不抛
    subscribe(req)                            # 软防护：非 LOGGED_IN 态 no-op（幂等重放需要，勿抛）
    close()                                   # DEAD 落位（幂等；连接在则先尽力 logout 清服务端会话槽）
    state -> SdkState                         # onDisconnected 余音甄别：登录后 5s 内断开回声跳过（防新鲜会话 churn）
```

### md_session.py（L2 会话层契约，韧性分层模型——L1 systemd / L2 本模块 / L3 reconciler）

```python
class MdSessionBase:            # 引擎只依赖本契约；平台知识（日切时刻/重登手法）封子类——接新平台=实现子类
    renew() -> bool                     # 换新会话（幂等；返回是否发起）
    schedule_due(now=None) -> bool      # 定时续航时刻已到（子类持时刻表+当日去重）
    retry_ready(now=None) -> bool       # 反应式重登退避已到点
    on_recovered()                      # 数据恢复清退避（_last_retry_ts 一并清——防恢复日志刷屏）
class XtpMdSession(MdSessionBase):      # 续航窗口 09:10-09:30（盘中启动不续航——防新鲜会话 churn）；
                                       # 反应式退避 30s 指数封顶 300s；续航未确认当日标记回滚（窗口内按退避重试）
is_trading_day(today=None) -> bool      # 交易日判定+按日缓存（D2 三坑规约：键=参数 date/只缓存 DB 成功读/
                                       # _reset_td_cache() 测试钩子；日历盘中变更最迟次日生效——知情落档）
zombie_session(sess_now, sess_ticks, sess_enter_ts, now, trading_day, grace=600) -> bool
                                       # 僵尸会话判定唯一实现（时段+交易日+零 tick 超宽限；有过 tick 再断流不在此列）
set_config_provider(_market_config_provider)   # quant_common.session 的市场配置回调（DB 侧注入，层 0 不碰 DB）
```

---

## 二、内部 API（不保证稳定）

- `strategy._init_factors(factor_configs)` - 从配置初始化因子（DSL 或注册表）
- `factor._FACTOR_REGISTRY` / `_safe_eval` / `_DT_OPS` / `_DT_FUNCS` / `_FACTOR_SAFE_BUILTINS`
- `factor._AstBlacklistChecker` / `_make_factor_class`
- `factor.CATEGORY_COMPAT` / `FACTOR_EXCLUSIVE` / `filter_factors_by_category`
- `adapters._vnpy_exchange` / `XTPAdapter._on_*`（事件回调）/ `_wait_update(cache, before, timeout=2.0) -> bool`（False=超时且已告警，P2-1 超时可观测）/ `POSITION_STABLE_WINDOW_S`（持仓稳定窗常量，测试可 patch）/ `parse_vt_symbol`
- `backtest.BacktestAdapter.set_bar/set_commission` / `BacktestEngine._calculate`

---

## 三、依赖

| 依赖 | 用途 |
|---|---|
| `risk_control.risk.RiskControl` | `place_order` 前置 `check_order`（延迟 import） |
| `data_platform.schema` | `to_vt_symbol`（adapters） |
| `data_platform` | `get_bars`（回测取数） |
| `data_platform.db` | 自定义因子 CRUD（factor_def 表） |
| `data_platform`（lazy） | md_session：市场配置回调/交易日历（is_trading_day 缓存） |
| `alert_notify.notify` | runtime.alerts：safe_notify（make_alert/make_guard） |
| `quant_common`（guard/session） | runtime.alerts 守卫包装；md_session 注册市场配置回调 |
| 外部 | `vnpy`（XTPAdapter/md_api_guard 延迟 import）/ `numpy` / `pandas` / `redis`（make_valkey） |

---

## 四、被谁调用

| 调用方 | 调什么 |
|---|---|
| `web_api.main` | 策略 CRUD + 实盘任务 CRUD（`/api/live-task`）+ 回测 + 因子 CRUD + 参数校验 |
| `md_hub`（批 2 起） | runtime 五模块（loop/pulse/mdlink/alerts/subs）+ `GuardedXtpMdApi` + `XtpMdSession` + `build_xtp_setting` |
| `strategy_runner`（C2） | 读 live_task + strategy_snapshot → `Strategy.from_config` → `on_bar`；批 4 起另消费 `GuardedXtpMdApi`/`XtpMdSession`/runtime（loop·pulse·alerts·xsleeper，worker 侧） |
| `scheduler.tasks` | `backtest_symbol_task` 合并 symbol_params → `BacktestEngine.run` |
| `astock_analysis` | `DailySelectionEngine`（用 `list_factors(static_only=True)`） |

---

## 五、读写表

| 表 | 写 | 读 |
|---|---|---|
| `strategy_config` | `web_api`（策略 CRUD） | `web_api`（创建 live_task 读快照）/ `scheduler`（回测） |
| `live_task` | `web_api`（实盘任务 CRUD）+ `strategy_runner`（status） | `strategy_runner`（启动读） |
| `factor_def` | `web_api`（自定义因子 CRUD） | `load_factors_from_db`（启动加载） |
| `backtest_runs` | `web_api`（含 symbol_params） | `scheduler.tasks`（回测逐标的） |
| `signal_log` / `order_log` | 策略 place_order | `risk_control.reconcile_three_books` |
| `account_snapshot` | `strategy_runner`（每 60s） | `risk._get_global_state` / Dashboard |

---

## 六、不变量

- **防未来函数**（铁律）：`BarContext` 只有当前 bar + `_history`（过去），**无 future 属性**；`validate_no_future_data` 断言；回测引擎 `history.append(bar)` 在 `on_bar` 之后
- **on_bar 签名**：`on_bar(bar: dict, history: list[dict] | None)` --回测/实盘一致
- **DSL 白名单**：`_DT_OPS`/`_DT_FUNCS`（算术 + abs/max/min/sum/round/float/int），禁 import/任意调用；表达式 ≤500 字符
- **Python 代码框安全**：`_check_ast_blacklist` 禁 import/exec/eval/open/__import__；运行期受限 `__builtins__` namespace
- **品类兼容**：crypto 因子不能跑 A 股（`validate_strategy_factors`，`FACTOR_EXCLUSIVE`）
- **因子静态/动态**：`needs_history=0` 静态可选股+策略，`>0` 动态只能策略；选股引擎 `static_only=True`
- **策略快照隔离**：`live_task.strategy_snapshot` 创建时固化，改 `strategy_config` 不影响已跑任务
- **回测跳风控**：`BacktestEngine.run` monkey-patch `Strategy.place_order`
- **事件驱动查询**：`XTPAdapter` 调 `query_position/account` 后轮询等 EVENT
- **三级开关**（实盘 AND）：`check_order` 前置 `.env` 总闸 + Web 分项 + 策略 enabled+backtest_verified

---

## 七、扩展指南

### 加新预置因子
1. `@register_factor("name", category="trend", params={"n":20}, needs_history=20)` 装饰 `Factor` 子类
2. 实现 `compute(ctx) -> float`
3. 自动进 `list_factors`；品类兼容 + 静态/动态自动

### 加自定义因子（Web，无需改代码）
1. Web 因子库页 → 新建 → 写 `compute(ctx, **params)` Python 代码 + 声明 needs_history
2. `_check_ast_blacklist` 校验 → `register_custom_factor` 存 DB + 进注册表
3. DSL/Python 策略均可引用

### 加新策略
1. 继承 `Strategy`（或直接用 DSL/Python 代码框双模式，不改代码）
2. `strategy_config` DB 配（factors/aggregator/params/parameter_defs）
3. 创建 `live_task`（选策略+标的+参数值）或 `backtest_runs`（多标的+per-symbol 参数）

### 加新执行适配器
1. 继承 `ExecutionAdapter`，实现 `send_order`/`cancel_order`/`query_position`
2. `create_adapter` mapping 注册

---

## 最近变更
- 2026-08-27（批 4c）：批 3 挂账清偿——runtime 六模块（含批 4b xsleeper）/md_api_guard/md_session public 面回写；3c 改名 enginekit 注记（未做，契约按现名）；依赖/被调表补 runtime 消费方
- 2026-08-09 初版
- 2026-08-11 因子平台化（DB 自定义）+ 静态/动态区分 + Python 代码框 + 参数定义系统 + 策略与任务分离（live_task）


## 增量（2026-08-19 链条打磨）
- **执行规则方向感知**（`_resolve_volume`）：SELL=持仓口径（PERCENT 持仓×pct/ALL_IN 清仓——position_snapshot）/ BUY=可用资金口径（总资产−持仓市值近似）；失败降级 SHARES 100
- **因子模式 Signal 回填**：聚合后自动填 price=close + volume（按规则推导）+ price_type/order_validity（从 params 读——此前恒默认）
- **DSL 窗口函数**：mean/std/max/min/ema/rsi/slope/avevol（AST 预处理：窗口位裸 Name→字符串；嵌套/表达式入参/未知名抛异常）
- **double_low 真实现**：convertible_terms 转股价+bar_1d 正股昨收 → 转股价值溢价
- `_warmup_history(symbol, n=100)` 窗口参数化（上限 500）
