# 批4：worker 迁移骨架 + trading.py 解互指 + direct 冻结（重排后第 4 批）

> 依据：12 号 §2.10 重排（原批 3 缩窄）。八步法（本文件=步 1 产物，步 2 双盲审后才准编码）。
>
> **v2（2026-08-27 步 2 双盲审修订）**：A/B 双同"需修订后复审"（无 P0，方向全成立）。
> 修订吸收 P1×6（sleeper never-raise/停止路径/钩子清单+映射表/测试声明/freeze 措辞/回滚）
> 与 P2 全项（源头门/缓存键/字段定案/NOGROUP/G2 道具/G5 观察对象重界定）。
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

### D1：mdlink 断流 in_session 门控——**源头统一门（v2：双盲审定案）**
- 现状：hub 以「盘中才喂 `counters.on_data`」规避夜间回放断流误告警（批 2 实证方案），mdlink 断流分支本身不门控
- **决策（v2 精化）**：`_tick` 段首统一取 `stalled = counters.stalled(now) if in_session else None`——一次覆盖段 2 症状腿与段 4 告警腿（双盲审：只门其一则未来无条件喂引擎夜间仍 renew 刷退避）
- **等价性证明（A 核实更强）**：hub 出沿当拍清 sess_last_ts → stalled() 恒 None → 对现 hub 该门是结构性空操作（非近似等价），行为值不变铁律精确成立；防御价值仅在未来无条件喂的引擎
- worker 是 TD-only 无 MdSessionSupervisor——D1 现役受益者只有 hub（防御纵深）；worker 不接监督器（v2 写明防误读）

### D2：schedule_due 每步查 DB——**缓存下沉到 `is_trading_day` 本体（v2：问题比自估更大）**
- 现状（B 实测）：hub `_td_cache` 只盖 `sup.tick` 参数——`XtpMdSession.schedule_due()` 内部**另有一次裸打 DB**（md_session.py:167），hub 每 5s、direct 每 10s 都在裸查
- **决策**：按日缓存下沉进 `md_session.is_trading_day()` 本体——一处修，hub 消重+direct 消裸查（worker 无 MD 会话不调——"三引擎"系笔误，v2 勘正为 hub+runner）
- **三条坑的规约（v2 双盲审补）**：
  ①缓存键=**参数的 date**（非 now().date()——schedule_due 显式传 now、测试传固定日期）
  ②**只缓存 DB 成功读**——失败回退 weekday 值不缓存（否则假日撞 DB 抖动被当交易日冻结一天）
  ③模块级缓存提供**清除钩子**（`_reset_td_cache()`）——测试可重置，防跨测试污染（A：mock 后缓存脏值=假绿假红）
  - 知情接受差异落档：日历盘中变更最迟次日生效；与 in_session 的 60s 缓存新鲜度不一致（hub 现状已如此，等价）

### D3：worker 的 on_fatal / 心跳字段定案（v2：双盲审改判"只写自有字段"）
- worker 迁骨架后 `EngineLoop(name="live-task-{tid}", on_fatal=…)` 告警接线（批 2 已加的 on_fatal 能力首次全引擎覆盖）
- **心跳（v2 定案）**：worker 只写自有 7 字段+ts（`{pid,md,gen,last_bar_ts,lag,bars,frozen,ts}`）——
  **不写 direct 专属字段**（`ticks/sess_ticks/last_tick_ts` 在 worker 语境无 tick 源，拿 bar 冒充 tick 是语义造假）。
  "超集"重定义=两模式字段并集皆合法（consumer 按 `md` 字段区分），非 worker 写全并集
- 消费方实测（B）：collector 只读 `{md,bars,lag,frozen}` 四字段；direct 专属字段无 task 级消费者——锁测试如实锁四个+残留断言（同键切模式旧字段残留至 TTL，无消费者读，无害落档）

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
