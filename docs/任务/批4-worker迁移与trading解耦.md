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
3. **direct 模式冻结**（v2 措辞修死）：**循环结构与节奏不动**；交易件调用点重接 trading.py（语义零漂移由测试保证）；trading.py 内修复=改 trading.py 双模式受益；direct 独有 bug（如 _gated_send_direct）就地修；骨架化=不做。批 6 随 ST7 阶段 1 退役。**漂移面论断**：4a 后六件已单源化，残余双份仅循环脚手架（喂狗/事件检查/心跳）——冻结期可接受
4. **批 2 遗留三输入一并解决**：mdlink 断流门控定案 / schedule_due 每步 DB 同治 / 行为统一

## 依赖（就绪）
批 2 骨架在产（EngineLoop/MdSessionSupervisor/AlertPolicy/HeartbeatWriter/SubscriptionManager）；
批 3 管道在管（发布走 release.yml）；批 2 双盲审输入清单在手。

## 产出
| 文件 | 动作 | 内容 |
|---|---|---|
| `server/src/strategy_runner/trading.py` | 新建 ~260 行 | `write_trade_log` / `snapshot_cycle`（账户+持仓单事务，含 SB1 不写假值）/ `halt_edge_cancel` / `recalc_hook` / `stop_due`（tid/sid 双态单实现）/ `reconcile_orders`（runner 超集：在场委托+WAL 残留+成交补录） |
| `server/src/strategy_runner/hub_worker.py` | 重写 | 437→~280 行：`XReadSleeper` 接 `EngineLoop(sleeper=…)`；定时段（快照/熔断沿/因子重算/TD 重连沿/事件线程检查/心跳）全部 `loop.every()`；流消费本体留 run() |
| `server/src/strategy_runner/main.py` | 修改 | direct **循环结构不动**；交易件内联块改调 trading.py（~-150 行重接）；worker 分派接线新 hub_worker（v2 措辞消『不动 vs 删』矛盾）|
| `server/src/strategy_framework/runtime/` | 小改 | 见"设计决策"三条 |
| `server/tests/test_trading.py` | 新建 | 六共享件单测（从既有散测试收编+补）；**v2 如实声明**：`test_position_snapshot.py:158-170` 是源码文本断言（直读 main/hub_worker 源码找 `_flush_positions` 内联），内联挪走即红——**挂点测试须改接 trading.py**（"原测试零修改"不实，双盲 B 实锤）；test_hub_arch 的 frozen/buy_ok import 同理改挂 |
| `server/scripts/run_worker_smoke.py` | 新建 | G2 道具（v2 补）：本地 fakeredis 流+stub TD 起真 worker 进程，断言 XReadSleeper 节奏/钩子分发/心跳字段——批 2 曾因缺真机冒烟判 P1 的教训不复犯 |
| `server/tests/test_hub_worker_migration.py` | 新建 | XReadSleeper 节奏/钩子接线矩阵 |

## 限定范围
不碰：direct 主循环结构（冻结）、hub（批 2 已迁）、bar 口径。**frozen/buy_ok 归属（v2 定案消矛盾）**：函数体与测试整体搬 trading.py、main/hub_worker 改 import——语义零漂移由测试整体迁移保证（非『零修改』，挂点必改，见产出表 v2 声明）。

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

## XReadSleeper 规格（v2：补异常契约/停止路径/线程模型——双盲审 P1 双同主区）
```python
class XReadSleeper:
    """EngineLoop.sleeper 注入：xreadgroup block 读取 + 到期唤醒双节奏。
    block 毫秒数 = min(500, 距下一钩子到期剩余毫秒)——定时钩子不可能被繁忙流饿死
    （双盲 A/B 独立核证：sleeper 至多 500ms 必返 → loop 每迭代走 dispatch，
    5s 钩子最坏延迟=500ms+单批处理时长，与现 worker 等值）。"""
    def __init__(self, r, stream, group, consumer, on_batch): ...
    def __call__(self, seconds: float) -> None: ...   # EngineLoop 的 sleeper 协议
```
**never-raise 契约（P1 双同）**：`__call__` 内自吞 redis 异常（Timeout 归类静默；其他类
告警+睡 1s 重试——对齐现 worker L303-311 行为）。**禁止向 EngineLoop.run 抛出**：loop 的
sleep 位无 try/except（loop.py:111），异常传穿将命中 worker finally 的 `os._exit(0)`
=干净退出码 → systemd 不重启 → **任务静默死**（2026-08-20 A3 事故类）。
**NOGROUP 处置（v2 定案）**：遇 NOGROUP 以 `EX_TEMPFAIL=75` 退出交 systemd 重启 →
run() 启动段的组重建（现 L164-177，P0-3 语义）接手——复用 SA4 退避，替代现状的
1Hz 告警死循环永不恢复。
**线程模型（v2 写死）**：单线程同步——on_batch 在 loop 线程内联执行，与钩子同线程
（与现 worker 主线程 process_batch 一致；frozen/history 裸 dict 无并发险）。**禁止后台线程**。

### 停止路径（P1 双同——worker 是首个需优雅停的引擎）
EngineLoop.run() 是 NoReturn 无停止通道——**不得用 failure="exit" 接 stop**（exit 1 →
Restart=on-failure 拉起 = F-36 churn 倒退）。设计：
```python
def _stop_hook():          # loop.every("stop-check", period, _stop_hook)
    if trading.stop_due(...):
        # finally 等价清理（现 worker finally L432-437 语义）：xgroup_del + 告警
        ...; os._exit(0)   # 正常停止码——Restart=on-failure 不拉起（SA4 分类）
```

### 钩子全清单（v2 补全——A/B 双同"原清单缺四项"）
| 钩子 | period | 现位置（worker 行号） | 备注 |
|---|---|---|---|
| xread 流消费 | sleeper 注入 | 304-311 | run() 本体 |
| 停止检查 | 5s | 317-319 | **v2 补**（原清单漏） |
| 看门狗+事件线程 | 步进 | 316+419-424 | EngineLoop 内建 |
| 时段沿 sess_bar_wall 清零 | 步进 | 322-325 | **v2 补** |
| 盲视判定+告警（hub 心跳/bar 停更） | 步进 | 326-335 | **v2 补**（喂 frozen 字段） |
| 心跳写 | 5s | 337-346 | D3 字段定案 |
| 快照+持仓批 | 60s | 348-373 | trading.snapshot_cycle |
| 熔断沿撤单 | 步进 | 374-394 | trading.halt_edge_cancel |
| 因子重算 | 5s | 396-407 | trading.recalc_hook |
| TD 重连沿对账 | 步进 | 409-418 | trading.reconcile_orders |
| xautoclaim 僵尸认领 | 5s | 425-431 | **v2 补** |

## 验收标准
1. `pytest tests/ -q` 全量绿；分层 4 绿；pyflakes 零新增
2. `git grep 'from src.strategy_runner.main import' src/strategy_runner/hub_worker.py` 归零（互指消灭）
3. `wc -l hub_worker.py` ≤300 且 `grep -c 'counter %\|snap_counter' hub_worker.py` = 0
4. **G2 冒烟**：`run_worker_smoke.py` 本地真进程全绿（XReadSleeper 节奏/心跳字段/停止路径含 NOGROUP 75 退出）+ staging 彩排发布路径过（staging 波次源改 db 入 4b 产出表——v2 补）
5. G4 盘外部署 + **一交易日观察（v2 观察对象重界定）**：worker 无生产实例，但 **trading.py 有直接生产消费者=任务 8（direct）**——观察内容=任务 8 的快照/心跳/trade_log/停止指标（部署 task 波重启恰好行使启动对账路径；熔断沿仅熔断时行使）；另观察 hub（D1/D2 改动的在产面：零告警风暴+09:10 续航正常）

### 回滚（v2 补——四要素补齐；双盲 A/B 同指"revert 无生产风险"系误判）
- **零 DB 迁移**（trading.py 只写既有表）——回滚=代码级，走批 3 已建成管道（release 翻转/rollback-tasks，机制现成仅引用）
- **4a 回滚**：revert + 盘外重发布 → 任务 8 回内联版（波及 task 波重启）
- **4b 回滚**：worker 无产上实例零风险；但 D1/D2 动在产 hub/direct 共用层——revert + 重发布 + hub 重启（租约让位设计保证安全）
- 各切片独立 commit，单 revert 机制上够；回滚窗均须盘外

## mock 方式
XReadSleeper 用 fakeredis 流注入（deploy/tests 既有道具思想）；trading.py 六件用 MagicMock adapter/PG；frozen/buy_ok 既有测试零修改语义级整体搬。

## 实施切分
- **4a**：trading.py 提取+互指消灭（纯移动+测试收编+挂点测试改接；direct 循环结构不动、调用点重接）
- **4b**：worker 迁骨架+XReadSleeper+D1/D2/D3+staging 波次源改 db
- **4c**：文档（md_hub.md/strategy_framework 契约回写挂账清偿）

### 知情差异清单（v2 补——双盲 B 指出 reconcile『超集』非整体搬，行为变化必须落档）
| 件 | worker 现行为 | 统一后 | 性质 |
|---|---|---|---|
| reconcile_orders | 只告警在场委托 | 在场委托+成交补录+WAL 残留（runner 超集） | **行为增强**——启动与每次 TD 重连沿均变化，知情接受 |
| snapshot_cycle | 无 available_cash、两事务 | 含 available_cash、单事务（direct 形态） | 行为统一（worker 落库多一列，无消费者受扰） |
| 停止检查节奏 | worker 5s / direct 60s | 统一 5s（worker 值，更灵敏） | 值变化 |
| 熔断/重算告警文案 | 两版字句微差 | 统一 direct 版文案 | 文案统一 |
| write_trade 日志 | worker 版无 RETURNING 详情 | 统一 RETURNING 版 | 观测增强 |

## 参考文档
1. `docs/任务/批2-runtime骨架与hub首迁.md`（骨架契约+行为映射表范式）
2. `docs/architecture/12-实盘稳定性设计.md` §2.8/2.10
