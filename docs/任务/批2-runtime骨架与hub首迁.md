# 批2：runtime 共享骨架 + hub 首迁（运行时重构第 2 批）

> 来源：重构批准计划（12 号 §2.9）。目标：消灭三引擎复制主循环的漂移温床——骨架单点化
> 「时段沿/症状判定/L2 接线/告警节奏/心跳」五职责，hub 第一个迁上（行为值不变，风险隔离在数据面）。

## 目标
1. `strategy_framework/runtime/` 五模块落地（到期驱动钩子循环 / 会话计数器+心跳 / L2 监督器 / 告警策略 / 订阅管理），假时钟测试全绿
2. hub 主循环段重写上骨架，**行为值不变**（AlertPolicy 用 hub 现值做默认），心跳字段超集兼容
3. 双盲审 P1×2 并入：守卫加锁+intentional-logout 标记；阻塞 login vs WatchdogSec 处置
4. 双盲审 P2×6 快修

## 依赖（就绪）
批 1 ✅（GuardedXtpMdApi 在产、双盲审通过、生产稳定：hub/任务 8 双轨运行、ST7 重新计数中）

## 产出
| 文件 | 动作 | 内容 |
|---|---|---|
| `server/src/strategy_framework/runtime/__init__.py` | 新建 | 包出口 |
| `runtime/loop.py` | 新建 ~120 行 | `Hook`/`EngineLoop`（见接口契约） |
| `runtime/pulse.py` | 新建 ~110 行 | `SessionCounters`/`HeartbeatWriter` |
| `runtime/mdlink.py` | 新建 ~110 行 | `MdSessionSupervisor`（盲审 P1 加锁落位） |
| `runtime/alerts.py` | 新建 ~90 行 | `AlertPolicy` + `alert_factory`/`valkey_helper` 共享件 |
| `runtime/subs.py` | 新建 ~100 行 | `SubscriptionManager`（收编 hub `_sync_subscriptions` 语义） |
| `server/tests/test_runtime_loop.py` | 新建 | 假时钟节奏/异常策略/线程检查/sleeper 注入 |
| `server/tests/test_runtime_pulse.py` | 新建 | 沿/基线/zombie/stalled（吃 `md_session.zombie_session`）/心跳超集锁 |
| `server/tests/test_runtime_mdlink.py` | 新建 | 监督器接线矩阵（schedule_due/症状/恢复/加锁互斥） |
| `server/src/md_hub/main.py` | 重写主循环段 | 629→~300 行，钩子化 |
| `server/scripts/run_hub_smoke.py` | 新建 | hub 真进程 10 分钟冒烟（心跳 diff/bar 连续/收尾 logout） |
| `md_api_guard.py`/`broker.py`/`strategy_runner/main.py` | 小改 | P2 六项快修 |

## 限定范围
不碰：runner/worker 主循环（批 3）、bar 聚合口径与流 schema、心跳**旧字段名**（超集原则：只增不改）、告警通道设计、部署脚本（P1 归批 5）。

## 接口契约

**loop.py**
```python
@dataclass
class Hook:
    name: str; period: float          # 秒；0=每步
    fn: Callable[[], None]
    failure: Literal["log", "exit"] = "log"
    next_due: float = field(init=False)

class EngineLoop:
    def __init__(self, *, name: str, step: float = 5.0,
                 sleeper: Callable[[float], None] | None = None,   # worker 注入 XReadSleeper（批3）
                 event_engines: tuple = (),                        # 事件线程存活检查（单一实现）
                 fatal_exit_code: int = 1): ...
    def every(self, name: str, period: float, fn, failure="log") -> None
    def run(self) -> NoReturn
    # run 语义：睡到最近到期（默认 sleeper=time.sleep，上限 step）→喂狗→线程检查→到期钩子分发；
    # 钩子异常按 failure 处置（exit→critical+os._exit(fatal_exit_code)）；到期驱动废 counter%N 相位耦合
```

**pulse.py**
```python
class SessionCounters:                # 事件线程写 on_data / 主循环读——GIL 原子（vnxtpmd 全程持 GIL 已实证）
    def on_data(self, in_session: bool) -> None
    def apply_edge(self, in_session: bool) -> bool      # 进沿 True 并写 enter_ts+清基线（事故1 单点化）
    def zombie(self, now: float, trading_day: bool) -> bool   # 委托 md_session.zombie_session
    def stalled(self, now: float) -> float | None
class HeartbeatWriter:
    def __init__(self, r, key: str, ttl: int = 90, base: Callable[[], dict]): ...
    def beat(self) -> None                               # hset+expire；异常仅警告不致命
```

**mdlink.py**（盲审 P1：与 GuardedXtpMdApi 内部锁配合——守卫 connect/relogin/login_server/onDisconnected 四入口 RLock；监督器持「intentional」标记让 onDisconnected 让位）
```python
class MdSessionSupervisor:
    def __init__(self, session: MdSessionBase, counters: SessionCounters, policy: AlertPolicy, role: str): ...
    def tick(self, *, in_session: bool, trading_day: bool) -> None
    # 内聚 schedule_due→renew / 症状且 retry_ready→renew+告警 / 恢复(<60s)→on_recovered
```

**alerts.py**
```python
@dataclass
class AlertPolicy:                    # 阈值/节奏/分级单一来源；hub 现值做默认（行为不变）
    zombie_grace: float = 600.0
    stall_error: float = 300.0        # hub 现值；批 3 统一 120/300 双级
    zero_tick_alert_period: float = 150.0   # hub 现值
    stall_alert_period: float = 30.0
    recover_window: float = 60.0
def alert_factory(logger_name: str): ...     # 收编三份 _alert/_guard/_valkey
```

**subs.py**
```python
class SubscriptionManager:            # 收编 hub _sync_subscriptions（491-515 语义原样：15s diff+60s 全量窗+退订 flush）
    def __init__(self, api, desired: Callable[[], set[str]],
                 diff_every=15.0, replay_every=60.0, on_remove_flush=None): ...
    def on_reconnect_edge(self) -> None    # 重连沿强制全量重放
    def poll(self, now: float | None = None) -> None
```

## 验收标准
1. `venv/bin/python -m pytest tests/test_runtime_*.py -q` 全绿；全量 `pytest tests/ -q` 绿；分层 4 绿
2. 心跳超集锁：`test_runtime_pulse` 断言 writer 输出 ⊇ 消费方清单（hub 键 `{pid,gen,subs,ticks,bars,sess_ticks,dropped_pg,last_tick_ts}` 对照 `health_monitor/collector.py`）
3. `md_hub/main.py` 行数 ≤320 且 `grep -c 'counter %' main.py` 归零（相位耦合消灭的证据）
4. G2：服务器 `run_hub_smoke.py` 10 分钟——心跳键字段与旧版逐一 diff、bar_hub 连续插入、无告警风暴、收尾 logout（批 1 发现的僵尸会话问题）
5. G4：盘外部署，观察一个交易日（心跳曲线/告警静默/bar 连续/ST7 计数不受扰）

## mock 方式
假时钟：`loop._now = lambda: fake` 注入或 freezegun 等价手写（不引新依赖）；HeartbeatWriter 用 fakeredis 或 MagicMock r；supervisor 的 session 用 MagicMock（relogin/schedule_due/retry_ready/on_recovered 四面）。冒烟不 mock（真机门）。

## 参考文档
1. `docs/architecture/12-实盘稳定性设计.md` §2.8/§2.9
2. `docs/任务/批1-SDK状态机与冒烟基座.md`（守卫契约与双盲审结论——mdlink 加锁设计的输入）
