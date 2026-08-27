# 批4：worker 迁移骨架 + trading.py 解互指 + direct 冻结（重排后第 4 批）

> 依据：12 号 §2.10 重排（原批 3 缩窄）。八步法（本文件=步 1 产物，步 2 双盲审后才准编码）。
> 前置：批 3 观察日收官（2026-08-27 进行中）。

## 目标
1. **hub_worker 迁上 runtime 骨架**：437 行 worker 的 5s 定时段全部退化为 `EngineLoop.every()` 钩子（XReadSleeper 双节奏注入），主循环只留流消费本体
2. **trading.py 落地**：交易域六共享件从 main/hub_worker 提取，消灭 `hub_worker → main` 反向 import（main:357/370 实证互指）
3. **direct 模式冻结**：不迁骨架（修复照做、迁移不做）；批 6 随 ST7 阶段 1 退役
4. **批 2 遗留三输入一并解决**：mdlink 断流门控定案 / schedule_due 每步 DB 同治 / 行为统一

## 依赖（就绪）
批 2 骨架在产（EngineLoop/MdSessionSupervisor/AlertPolicy/HeartbeatWriter/SubscriptionManager）；
批 3 管道在管（发布走 release.yml）；批 2 双盲审输入清单在手。

## 产出
| 文件 | 动作 | 内容 |
|---|---|---|
| `server/src/strategy_runner/trading.py` | 新建 ~260 行 | `write_trade_log` / `snapshot_cycle`（账户+持仓单事务，含 SB1 不写假值）/ `halt_edge_cancel` / `recalc_hook` / `stop_due`（tid/sid 双态单实现）/ `reconcile_orders`（runner 超集：在场委托+WAL 残留+成交补录） |
| `server/src/strategy_runner/hub_worker.py` | 重写 | 437→~280 行：`XReadSleeper` 接 `EngineLoop(sleeper=…)`；定时段（快照/熔断沿/因子重算/TD 重连沿/事件线程检查/心跳）全部 `loop.every()`；流消费本体留 run() |
| `server/src/strategy_runner/main.py` | 修改 | direct 段 **不动**（冻结）；删被 trading.py 取代的内联块（~-150 行）；worker 分派接线新 hub_worker |
| `server/src/strategy_framework/runtime/` | 小改 | 见"设计决策"三条 |
| `server/tests/test_trading.py` | 新建 | 六共享件单测（从既有散测试收编+补） |
| `server/tests/test_hub_worker_migration.py` | 新建 | XReadSleeper 节奏/钩子接线矩阵 |

## 限定范围
不碰：direct 主循环（冻结）、hub（批 2 已迁）、frozen/buy_ok 语义（原函数原测试整体搬 trading.py，零漂移铁律）、bar 口径/心跳字段。

## 设计决策（三处，批 2 输入的定案）

### D1：mdlink 断流 in_session 门控——**加门控，hub 喂法不变**
- 现状：hub 以「盘中才喂 `counters.on_data`」规避夜间回放断流误告警（批 2 实证方案），mdlink 断流分支本身不门控
- **决策**：给 `MdSessionSupervisor._tick` 的断流症状分支加 `in_session` 门（零 tick 分支已有 in_session 门）——防御纵深：即便未来某引擎无条件喂 on_data，夜间也不误判。hub 的「盘中才喂」保留（语义等价的双保险，行为值不变）
- worker 接线时沿用 hub 喂法（盘中才喂）

### D2：schedule_due 每步查 DB——**缓存下沉到 `is_trading_day` 本体**
- 现状：hub 侧 `_td_cache` 按日缓存（批 2-4 实现），runner/worker 没有——每 5-10s 一次 DB 查 trade_cal
- **决策**：把按日缓存下沉进 `md_session.is_trading_day()`（functools 按日缓存，日切失效）——一处修，三引擎受益；hub 侧 `_td_cache` 删除（消重复）

### D3：worker 的 on_fatal / 波次心跳字段
- worker 迁骨架后 `EngineLoop(name="live-task-{tid}", on_fatal=…)` 告警接线（批 2 已加的 on_fatal 能力首次全引擎覆盖）
- 心跳：HeartbeatWriter 超集（worker 旧字段 `{pid,md,gen,last_bar_ts,lag,bars,frozen}` ∪ direct 字段）——**消费方清单锁测试**同步扩

## XReadSleeper 规格（批 2 任务文件遗留契约的落地）
```python
class XReadSleeper:
    """EngineLoop.sleeper 注入：xreadgroup block 读取 + 到期唤醒双节奏。
    block 毫秒数 = min(500, 距下一钩子到期剩余毫秒)——定时钩子不可能被繁忙流饿死
    （数学保证：block 上限收敛到最近到期点）。消息到达即回调处理。"""
    def __init__(self, r, stream, group, consumer, on_batch): ...
    def __call__(self, seconds: float) -> None: ...   # EngineLoop 的 sleeper 协议
```

## 验收标准
1. `pytest tests/ -q` 全量绿；分层 4 绿；pyflakes 零新增
2. `git grep 'from src.strategy_runner.main import' src/strategy_runner/hub_worker.py` 归零（互指消灭）
3. `wc -l hub_worker.py` ≤300 且 `grep -c 'counter %\|snap_counter' hub_worker.py` = 0
4. **G2 冒烟**（staging 彩排环境）：staging 波次源改 db 后（本批顺带，消第十坑盲区），彩排发布含 worker 钩子节奏验证（假流+假钩子矩阵已在单测，彩排验发布路径本身）
5. G4 盘外部署 + **一交易日观察**（worker 上的 live_task 8 转测 hub 模式留批 6——direct 冻结期任务 8 仍 direct，观察对象=无直接生产消费者时的回归面，以测试+彩排为门）

## mock 方式
XReadSleeper 用 fakeredis 流注入（deploy/tests 既有道具思想）；trading.py 六件用 MagicMock adapter/PG；frozen/buy_ok 既有测试零修改语义级整体搬。

## 实施切分
- **4a**：trading.py 提取+互指消灭（纯移动+测试收编，direct 不动）
- **4b**：worker 迁骨架+XReadSleeper+D1/D2/D3+staging 波次源改 db
- **4c**：文档（md_hub.md/strategy_framework 契约回写挂账清偿）

## 参考文档
1. `docs/任务/批2-runtime骨架与hub首迁.md`（骨架契约+行为映射表范式）
2. `docs/architecture/12-实盘稳定性设计.md` §2.8/2.10
