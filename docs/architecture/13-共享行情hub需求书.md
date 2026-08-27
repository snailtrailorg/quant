# 13 · 共享行情 Hub 架构 · 稳定性需求书（P-6 交付物 · 2026-08-17）

> ST7 的设计门禁。来源：稳定性检查矩阵 F-9~F-12、盲审报告 §4、方法论 §6-3、SA-SE 五批次实施经验。
> 每条需求带验收判据（可证伪）。设计（14 号文档）与实施（ST7）逐条对照本文，缺一条即不通过评审。
>
> **当前状态（2026-08-27）**：hub 已在产（批 2 主循环迁 runtime/ 骨架）；R-BR20 影子门禁处于
> **阶段 0 影子期**——重构批 1-5 部署日双轨缺口致有效日顺延，08-26 起重新计数；批 1 连带修复 ST7
> 双轨（XTP 同账号同 client_id 单会话规则 → runner 独立号 client_id_runner=2）。门禁过 → 批 6
> 收口切阶段 1 → direct 退役。计数状态：`flow/待办.md` 常规线 1。

## 0. 背景与定位

- 现状：每实盘任务一进程，各持 XTP 连接+全市场合约表（实测 400-600M/任务）——1.8G 服务器容量天花板 ≈1 任务。
- 目标：**一个 hub 进程**持 XTP 行情连接+合约表（~500M 一份），把分钟 bar 分发给 N 个轻量 worker（目标 ≤250M/个）；国内市场分钟级实时性足够。
- hub 是**纯数据面**：不做策略决策、不下单、不读风控——交易语义全部留在 worker（复用 SA/SB/SC 已建机制）。

## 1. 可用性与故障恢复（R-AV 系列）

| # | 需求 | 验收判据 |
|---|---|---|
| R-AV1 | hub 单点必须三件套：systemd 看门狗（WatchdogSec+sd_notify）+ Valkey 心跳键（tick 数/最新 tick ts/订阅数/bar 发送计数，TTL）+ OnFailure 告警 | 拔掉喂狗→90s 内 systemd 重启；心跳停更→巡检告警；Failed→飞书 |
| R-AV2 | worker 侧"失联即冻结"：hub 心跳过期/流断（>120s 交易时段）→ worker 禁止开新仓（只允许 SELL 平仓路径）+ 告警 | 注入：kill hub → worker 日志出现冻结标记，无新 BUY 单 |
| R-AV3 | hub 重启恢复：重新拿租约（代次 gen+1）→ 补当日缺口（自身缓存/PG bar_1min）→ 重新分发；worker 不重启、不丢因子状态 | kill -9 hub → 恢复后 worker 收到连续 bar（seq 无跳变或显式补齐段） |
| R-AV4 | 重启风暴防护同 SA3：StartLimit 5/300s + 失败告警 | unit 配置项存在 |

## 2. 分发语义（R-DL 系列，核心）

| # | 需求 | 验收判据 |
|---|---|---|
| R-DL1 | **at-least-once + worker 幂等 = 恰好一次处理**：worker 按 (symbol, ts) 去重（复用 SC bar 级幂等） | 注入重复消息 → on_bar 只执行一次 |
| R-DL2 | **有序**：每 symbol 严格按 ts 升序；每消息带 hub 序号 seq；断序检测→冻结交易+从流回放补齐 | 注入乱序/丢消息 → worker 检出 seq 跳变并补拉 |
| R-DL3 | **防陈旧**：消息带交易所时间戳+hub 发送时间戳；worker 对超龄 bar（交易时段 >60s）丢弃+告警，绝不进 on_bar | 注入旧 bar → 丢弃+告警日志 |
| R-DL4 | **fencing 防脑裂**：租约（Valkey key，TTL 30s，10s 续）+ 每消息带代次 gen；worker 记录已见最大 gen，拒绝 gen 更小的消息（旧 hub 复活） | 注入双 hub（旧进程残留）→ worker 拒旧代次并告警 |
| R-DL5 | **慢消费者背压**：worker 落后时（流积压）→ 批量追赶+超龄丢弃，不无限排队占内存 | 断网 worker 5min 后重连 → 快速追平或按 R-DL3 丢弃 |

## 3. 订阅管理（R-SUB）

| # | 需求 | 验收判据 |
|---|---|---|
| R-SUB1 | 订阅真相源=DB（running 且 hub 模式的 live_task 的 symbol 集合）；hub 周期（30s）diff 增删订阅；订阅幂等重放（复用 SA2） | 新建任务 → ≤30s hub 订阅新 symbol；停任务 → 退订 |
| R-SUB2 | hub 断线重连后自动重放全部订阅（SA2 语义在 hub 侧同样成立） | 注入 XTP MD 断线 → 恢复后订阅齐全 |

## 4. TD 会话隔离（R-TD）

| # | 需求 | 验收判据 |
|---|---|---|
| R-TD1 | hub 用**独立 client_id**（与 worker 的 TD 会话不同号），杜绝 hub 上线踢掉 worker 交易会话 | hub+worker 并存运行，worker TD 无断线日志 |
| R-TD2 | XTP TD 会话约束文档化：同账号同 client_id 互踢；v1 强制 **account 级唯一**（同 XTP 账户同时只允许一个 worker 持 TD），严于 ST3 的 (account,symbol) | 建第二个同账户任务 → 拒绝并提示 |

## 5. 容量与资源（R-CAP）

| # | 需求 | 验收判据 |
|---|---|---|
| R-CAP1 | worker 不加载 XTP SDK/合约表（不 gateway.connect）→ RSS ≤250M | 服务器实测 systemctl show MemoryCurrent |
| R-CAP2 | hub MemoryMax 单独定量（合约尖峰预算 1G）+ 2G swap 前提 | unit 配置 + 实测 |
| R-CAP3 | hub 顺带把分钟 bar 落 PG（bar_1min 批量 upsert）——数据中台分钟线从实盘自采（修服务器空表问题） | 次日 bar_1min 有当日数据 |

## 6. 暖机语义（R-WARM）

| # | 需求 | 验收判据 |
|---|---|---|
| R-WARM1 | worker 盘中重启暖机=**从 hub 流回放当日 bar**（PG 分钟线是盘后同步的，盘中 PG 是昨日数据——盲审实锤）；PG 仅隔日历史兜底 | 盘中重启 worker → 因子窗口含当日完整序列 |
| R-WARM2 | hub 自身重启的暖机=PG 当日已落库部分+重连后实时（分钟级窗口可容忍） | hub 重启后 bar 序列连续（R-AV3） |

## 7. 停止/熔断语义（R-HALT）

| # | 需求 | 验收判据 |
|---|---|---|
| R-HALT1 | hub 纯数据面：不执行熔断/停止判断；worker 侧 check_order/熔断沿撤单照旧（SB 机制零改动） | 代码审查 hub 无 risk_control import |
| R-HALT2 | worker 停止=照旧 live_task.status 检查；hub 不因 worker 停止而退出 | 停任务 → hub 继续服务其他任务 |

## 8. 迁移与回滚（R-MIG）

| # | 需求 | 验收判据 |
|---|---|---|
| R-MIG1 | 双模式共存：system_config `md_mode`（direct|hub）全局开关；direct 路径（现架构）保留 ≥1 迭代可随时回滚 | 开关切换 → 任务重启后按新模式运行 |
| R-MIG2 | 迁移顺序强制：起 hub → 观察（订阅/心跳/bar 正常）→ 切开关 → 重启任务 → 验证；绝不先停旧任务再起 hub | 运维手册含 checklist |
| R-MIG3 | 部署闸门（SE3）对 hub 同样生效：交易时段禁变更 | 闸门代码覆盖 quant-md-hub |

## 9. 可观测（R-OBS）

| # | 需求 | 验收判据 |
|---|---|---|
| R-OBS1 | hub 心跳键：pid/gen/订阅数/最新 tick ts/bar 发送计数（TTL 90s）；巡检任务扫停更→告警（并入监控清单） | 手动删心跳键 → 90s 内告警 |
| R-OBS2 | worker 心跳键增加 md=hub 字段与当前 gen/落后量 | 键存在 |
| R-OBS3 | P-5 注入清单新增：hub kill -9 / 旧 hub 复活（双进程）/ 消息重复投递 / 断流 5min / worker 慢消费 | 注入用例文档化 |

## 10. 非目标（明确不做，防蔓延）

- hub 不做订单路由/集中下单（TD 集中化是二期，另立决策）
- hub 不做 tick 级分发（v1 分钟 bar；tick 分发待真实需求）
- 不追求跨机部署（v1 同机 Unix/Valkey 通信；跨机是二期）

---

## 11. 盲审合并增量（评审代理 ①，2026-08-17，全文 `flow/稳定性检查/盲审hub需求-代理A.md`）

> 与 §1-10 收敛项不重列。以下为盲审独有、已采纳并入的需求（编号续 R-BR*）。

### 开工三前提（gate，不通过不动工）

| # | 前提 | 判据 |
|---|---|---|
| R-BR1 | **vnpy_xtp 网关 MD/TD 耦合**：gateway.connect() 同时连行情+交易——"worker 只留 TD"需绕过整装网关（直接构造 TdApi、不启 MdApi/MainEngine/合约查询），行为需实测 | worker 进程 MD 会话数=0、零合约表加载的实测记录 |
| R-BR2 | **TD-only worker 内存收益实测先行**：若实测 RSS >300M 则改造成立性存疑，重新评估（vnpy core+TdApi .so 底盘可能 150-300M） | 单 worker RSS 报告先于全面实施 |
| R-BR3 | **券商并发 TD 会话配额外部确认**：v1 以 account 级唯一（1 worker/account）规避；N 账户 N worker 场景需券商确认 | 外部 gate 项（并入待办外部清单） |

### 分发与数据质量增量

- **R-BR4 断线跨分钟 bar 失真**（A9）：XTP 断线不回补行情，跨断线那根分钟 bar OHLCV 不完整——hub 必须丢弃或标记"不可信"并触发冻结/补采，绝不把失真 bar 放大给全员
- **R-BR5 分钟边界显式 flush**（A8）：收盘尾 bar（15:00）/真空分钟由 hub 定时 flush（:05）显式投递或显式空洞标记，不依赖次日 tick（F-38 根治）
- **R-BR6 epoch=gen 重置语义**（A10）：hub 重启 gen+1 且 seq 重置，worker 见 gen 跳变即重置序号基线（不误判乱序）
- **R-BR7 回放不下单**（A4）：hub 回放补齐当日 bar 时 worker 因子更新但**零下单**（发布时间戳标记 replay=true）
- **R-BR8 慢消费者隔离**（A6）：per-worker 有界队列+溢出断开告警（Valkey stream 消费组天然支持，禁止 worker 本地无界缓冲）
- **R-BR9 回测口径对齐**（F7）：hub 分钟归属（tick 时间→分钟桶）与 Tushare 分钟线口径抽样对比 ≥20 交易日全等或差异归因

### worker 侧增量

- **R-BR10 client_order_id 全局唯一**（C4）：`c{seq}` 进程内序号在 N worker 冲突——改 `{task_id}:{epoch}:{seq}` 前缀，order_log/成交归属不错认
- **R-BR11 TD 重连后重跑对账**（C5）：SC2 启动对账在 TD 会话重连后同样触发（不限进程启动一次）
- **R-BR12 事件线程防护迁移**（C8）：_guard/线程死亡检测复制进 worker（TD 事件仍走 vnpy 事件线程，F-26 在 worker 依旧成立）
- **R-BR13 熔断沿 N 节点传播**（C9）：各 worker ≤5s 检测熔断（10s 主循环保留）+告警经 notify 1min 去重天然防风暴

### 运维与安全增量

- **R-BR14 告警责任反转**（F2）：hub 死时由 worker 承担告警（hub 无法自报）；聚合抑制靠 notify 去重（已有）
- **R-BR15 hub 重启不连带 worker**（C6）：单元间禁 PartOf/BindsTo；整机重启自愈顺序 After= 链 PG→Valkey→hub→worker（E6）
- **R-BR16 account_snapshot 单写者**（F3）：v1 account 级唯一约束天然保证单写；写进运维手册为不变量
- **R-BR17 Valkey noeviction 确认**（D9）：bar 流所在实例禁 eviction（静默丢 bar 比崩溃危险）；MAXLEN 有界（D8 环形缓冲）
- **R-BR18 hub 控制面限流**（F10）：订阅注册只认 DB 真相源（R-SUB1 已含），不接受进程自由 SUBSCRIBE 洪水
- **R-BR19 引用数据按需分发**（D5/F9 降级采纳）：v1 worker 下单不依赖合约元数据（价格不对齐由用户策略负责）；tick_size 类需求出现时二期
- **R-BR20 影子对比门禁**（E2）：正式切流前 hub bar vs 旧进程 bar 逐根 diff ≥5 交易日零差异（或归因书面接受）
- **R-BR21 零 tick 窗口感知**（F8）：hub/worker 断流升级沿用"今日已收过 tick"+交易时段判断（SA1 语义），午休/停牌不误杀
