# D32 复审 A2（架构正确性 · 只核 delta）

## ① 闭环
- **P0-4 角色化**：§0.2#3 / §5.1 / §9.3 三处一致 `build(account, role, slot)`，Runtime 按 role 只暴露合法端口 + `UnsupportedCapability`，S8 进程内断言 + 真起停 → R-TD1 有守门载体。
- **P0-8 推送**：§六 ExecutionPort 增 `subscribe_order/subscribe_trade`，§179 钉「推送为主/查询只作对账恢复」+ 领域状态枚举不暴露 vnpy `Status`。
- **P0-2 主体**：§七 改既有表映射（market_hours / security_master / band_rules），撤销 market_spec / instrument_spec，settlement 删除、T+N 归 `trade_phase` 单源 → 时段/tick/T+ 三处重复已各自单向。
- **P0-1 部分**：Account 维已立（§0.2#4 / §五 / §102），准入有 `perms.account_allows` + `market_op` 归属，停牌态有「停牌表 + 事件流」归属。

## ② 未闭环
- **P0-1 残留**（§五/§100/§7.3）：真源列 `perms + trading_account + 停牌表` 三者均无行情授权等级字段，§3.1 归属表无该行 → 数据授权**无源、无消费点、无 fail-closed**；可用资金/保证金只在维内点名，执行序列 `check_order→WAL→validate` 仍无现金/保证金硬门。
- **P0-2 残留**（§7.5 对 §7.2）：`close_policy.*` 按 provider 分键（xtp/emt 各一套，0175），迁 `security_master`（品种表）= 跨维错位，同标的两通道互相覆盖；且这些列不在 §7.2 缺列清单 → 迁移清单未闭合。
- **P0-3**（§7.2/§7.3/§十五-2）：只有悬空引用 `MarketSpec.order_price_range`；§7.1 映射无此字段、无 `exchange[:board]×phase` 结构、无合成顺序、无缺配置 fail-closed；与此同时 instrument `price_range` 列与它并存且无优先级 = 新的两源。
- **P0-5**（§9.5/§十二）：allocator 键 `slot:{account_id}:{provider}:{role}` 缺任务/实例维。真实拓扑是同账号 N worker 各一 TD 会话（`runner_client_id(tid)`、`td_registry.py:63/131`），按 account×role 加租约 → 第二个 worker 拿不到槽或拿同槽撞 client_id；`t{tid}:e{epoch}:` 的 tid 来源未定，「禁止公式回落」撤了现行唯一性机制却无替代。§十二 per-(account,role) 互斥会同样误锁多任务。
- **P0-6**（§六）：MarketDataPort 缺 `connect`（`md_hub/main.py:233` 显式 `connect(cred,params)`，而 ExecutionPort 有 connect——连接归属两说）、缺 `unsubscribe`（`main.py:325` 订阅 diff）、缺 `set_context`（`main.py:333`）、`poll_supervise()` 丢 `trading_day` 入参（`main.py:418`）；`event_threads_healthy` 现网无消费者（`md_gateway.py:500` 为网关内自检）。
- **P0-7**（§7.3/§六）：交易所态写「插件事件流 + 停牌表」，但插件在 hub(MD)、`pre_trade_checks` 在 worker(TD)；worker 只消费 `hub:bars`（`hub_worker.py:28/96`），bar 无状态位、§六 Tick 领域模型未定义 halt/status、跨进程停牌态通道（DB/流）未定义。`venue_flags` 缺列未定义字段，旧稿 convertible_halt 静态布尔与动态机制的冲突未澄清。

## ③ 新引入
- **§7.4 fail-open 空转**：规则行缺失时 `band_rules` 的跌停线同样不可得，「fail-open 到跌停线/对手价」不可执行，需写死兜底（如 ±10%）或改语义。
- **§81/§120 与 §7.3 自相矛盾**：规则层声明 `RuleSet = MarketSpec × InstrumentSpec × AccountSpec × ProviderCaps`，而 §7.3 的 RuleSet 类无 caps 字段——能力协商被混进每笔冻结快照（原 P1-11 未解，重写后成了显式设计）。
- **§5.1 `assert plugin.capabilities ⊇ need`**：生产 assert 可被 `-O` 剥离，与 S6「不足抛 UnsupportedCapability」不一致。

## 结论
仍须改
