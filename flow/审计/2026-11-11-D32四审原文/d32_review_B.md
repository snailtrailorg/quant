# D32《接入插件架构》可实施性 / 迁移盲审（审官 B）

- 审查对象：`docs/design/D32-接入插件架构.md`（2026-11-10 定稿，未实施）
- 方式：只读代码，不修改仓库；对照 prod 现行结构逐条核验
- 代码基线：`74b169a`（main）
- **结论：须改后再审**

理由：D32 的两项决策（vnpy 内化、规则声明式）方向正确，但按现文字无法立项——(1) 注册表/能力源已有 8 处注册点，D32 的 `REGISTRY` 会成为第 9 处而非唯一处；(2) vnpy 在本仓不只是 SDK，而是**应用层的事件/数据通用语**（EMT 这种纯 SDK 插件也在合成 vnpy 对象推事件），端口又没有事件通道，S1「通用层零 import vnpy」在 P1 不可能成立；(3) `build(account)` 无角色参数，hub 会顺带建 TD，直接破 R-TD1；(4) 号段无跨进程分配契约，多 runner 必撞号；(5) P1「只搭骨架、不改行为」与 S1 自相矛盾。

以下 P0/P1 均给「D32 位置 → 真实代码证据 → 一句话修法」。

---

## 一、P0（立项阻断，必须先改 D32）

### P0-1　`REGISTRY` 未收编既有注册点与能力源，manifest 会成第五真源

- D32：§十 `REGISTRY: dict[provider_id, ProviderPlugin]`（:272）、「启动校验 fail-fast」（:276）、§九.1「manifest 唯一声明面」（:221-232）。
- 真实代码：同一 provider 概念现由 **8 处**各自注册/声明——
  1. `data_platform/data_source.py:374` `_REGISTRY`（DataSource）
  2. `data_platform/adapters/base.py:559` `_ADAPTERS`
  3. `data_platform/interfaces/base.py:58` `_PROVIDER_MODULES` + `_REGISTRY`
  4. `strategy_framework/md_gateway.py:40,115` `_REGISTRY`（MD 网关）
  5. `strategy_runner/td_registry.py:17` `TD_BUILDERS`
  6. `strategy_framework/broker.py:75` `_REGISTRY`（Broker 凭证）
  7. `strategy_framework/adapters.py:978-983` `create_adapter` 映射
  8. 能力/市场四张纯数据表：`quant_common/markets.py:51` `CAPABILITIES`、`:121-126` `NON_DATA_PROVIDERS`、`:129-140` `PROVIDER_MARKET`、`:152-155` `DOMAIN_CAPS`；能力读取另有 `data_platform/capabilities.py:16-30`（读 `_ADAPTERS`）。
  代码里已有明证：`data_source.py:415-423` 报错文案写着「加新源须**三处各注册一类**」；`md_gateway.py:120-130` 已有按 provider 的插件取用。
- 后果：manifest.capabilities 与 `capabilities.provider_capabilities()` 不同步时，集成中心写侧 ⊆ 校验（`web_api/routes/mgmt.py:162`）和插件运行时能力协商会得出相反结论；S3/S6 全部落空（删包后旧注册点仍然解析成功）。
- **修法**：D32 §十改为「单注册表 + 三面视图」：`REGISTRY` 是 provider 身份、能力、config_schema、transport 的唯一声明处，P1 同时把上述 8 处改成**投影/适配**（P1 只读投影，P3/P4 再逐项删除），并新增守门「任一 provider 在 REGISTRY 与 legacy 注册点中的能力集必须相等，或显式标记 legacy-only」。

### P0-2　端口契约缺「事件推送」，而 vnpy 是应用层通用语 → S1 不成立

- D32：§六 `ExecutionPort`（:96-104）只有 send/cancel/query_*/order_id_map；`MarketDataPort`（:106-109）只有 subscribe/snapshot/stream。§四要求应用/编排「不认 vnpy」（:47-48,62）。
- 真实代码：worker 的交易链不是轮询，而是**事件驱动**：
  - 编排层自建并注册 vnpy 事件：`strategy_runner/main.py:18-21`（模块级 `EventEngine`）、`:342-352`（`EVENT_LOG`）、`:355-366`（`EVENT_ORDER`）；`hub_worker.py:455`（`__import__("vnpy.trader.event")` 注册 `EVENT_TRADE`）。
  - 应用层消费 vnpy 对象形状：`strategy_runner/trading.py:88`（`vt_orderid`）、`:131`（`d.direction == Direction.LONG`）、`:155`（`d.datetime`）、`:302`（`od.vt_orderid`）；`trading.py:81,128,296,514,780` 直接 import vnpy `Status/Direction`。
  - EMT 是**不用 vnpy 网关**的插件，却被迫合成 vnpy 对象推事件：`adapters.py:494-498`（注释明说「vnpy 形状契约」）、`:756-763`（`vnpy.event.Event` + `EVENT_ORDER/TRADE`）。
- 后果：不定义事件端口，P1 要么继续在 `main/hub_worker/trading` import vnpy（S1 红），要么改这三处消费面（就不是「不改行为」）。当前 D32 是两难。
- **修法**：`ExecutionPort` 增 `subscribe(on_order, on_trade)`（或 `events() -> Iterator[OrderEvent|TradeEvent]`）；新增 `quant_common/domain` 事件对象；P1 定义契约但**不接线**，P1b 再把 worker 三条链切到端口事件（见 P0-5 拆分）。

### P0-3　`ProviderRuntime.build(account)` 无 role/need，hub 会建 TD（破 R-TD1 并撞号）

- D32：§五 `driver = prov.build(account)`（:81）；§九.3 `ProviderRuntime: execution / market_data / research / account / history`（:252-254）；§九.3「按 capabilities 提供」。
- 真实代码：进程角色与连接面是**硬隔离**的——hub 只建 MD（`md_hub/main.py:230-233` `create_md_gateway`），TD 只在 worker 建（`td_registry.py:51-133`），这是 R-TD1「hub 零 TD」的代码级保证（`md_gateway.py:43-69` ThinGateway 的 7 个交易方法全 raise）。
- 后果：按 D32 字面，hub 调 `build(account)` 会实例化包含 execution 的 runtime → 在 hub 进程登录 TD（`client_id` 与 worker 撞、双 TD 会话、hub 可下单），直接违反 R-HALT1/R-TD1。
- **修法**：`build(account, need: set[str], role: Literal["md","td"])`（或 `build_market_data(account)` / `build_execution(account)` 两入口），并在 manifest 侧声明各能力所需的连接；S6 增断言「hub 进程 build 结果无 ExecutionPort」。

### P0-4　号段「归插件内部」缺跨进程分配契约，多 runner 必撞号

- D32：manifest `client_id: {mode: range, min: 2, max: 99}`（:230）；§9.5「manifest 声明策略、插件内部实现」（:266）；§9.3「连接窗/号段/重连全在插件」（:256）。
- 真实代码：
  - XTP 客户号派生 `broker.py:128-137` `(task_id-1)%98+2`；注释 `:131-133` 明言「同账户同 client_id 仅一个 TD session，后面的登录无法连接」。
  - EMT 派生 `td_registry.py:164-165` `(tid-1)%100+20`（域 1-127）；注释 `:143` 承认「真防线=tid 派生唯一性」。
  - hub MD 与 TD 共用账号行参数里的 `客户号`（`broker.py:151` 默认 1），worker 覆盖（`:200,212`）。
- 后果：插件实例天然是**每 worker 进程一个**，进程内分配器各自从 min 起 → 同账号多 runner 全撞；且 tid 间隔 98（XTP）/100（EMT）即回绕撞号。启动失败是显式的，但一旦未来「急停执行器」独立进程，就是同账号双 TD。
- **修法**：号段改为「插件 + 跨进程租约」：表 `provider_session_slot(account_id, provider, terminal_id, holder, boot_epoch, heartbeat)` 唯一 `(account_id, terminal_id)`，或复用 hub 租约原语（`md_hub/main.py:136-149,351-366` 的 Valkey 租约）按 `(account,terminal_id)` SETNX+TTL；分配失败 → EX_TEMPFAIL 退避，禁静默复用。manifest 只声明区间与「预留 hub MD 号」。

### P0-5　P1 自相矛盾：S1「全仓零 import vnpy」 vs「只搭骨架、不改行为」

- D32：§十一「P1 只搭骨架（双轨并存、不改行为）」（:295）；§十三 S1「除 providers/ 外 server/src 零 import vnpy」（:308）。
- 真实代码：当前 8 个文件 import vnpy：`md_hub/main.py:36-39`、`strategy_framework/md_api_guard.py:32-33`、`md_gateway.py:35-38,144-146,...`、`adapters.py:225,258,309-310,...`、`td_registry.py:59-60`、`strategy_runner/main.py:18-21,324,347,355`、`hub_worker.py:455,795`、`trading.py:81,128,296,514,780`。总量约 4,900 行，其中 `trading.py`（814）+`hub_worker.py`（978）+`main.py`（677）是行为核心。
- 后果：P1 若按 S1 全仓清零 → 必改交易/回报/查询链（行为变更）；若只建空骨架 → S1 红。无论哪种，「P1 双轨不改行为 + S1 绿」不可同时成立。
- **修法**：拆 P1a/P1b。P1a＝新目录（`quant_common/domain`、`ports/`、`registry/`、`providers/` 空架）+ 守门**只扫新目录** + 对存量 vnpy import 建 `KNOWN_VNPY_IMPORTS` 基线（file:line 冻结，只许减不许增）。P1b＝事件端口 + `trading/hub_worker/main` 接线（行为等价测试见 P1-2）。

---

## 二、逐条回答（是/否 + 证据 + 修法）

### Q1 vnpy 内化可行性；hub 本身算不算插件；P1 零 import 能否成立

- **内化本身：可行，但须按进程角色拆。**
  - MD 半边已基本是插件：`md_gateway.MdGateway` + `_REGISTRY` + `create_md_gateway`（`md_gateway.py:72-130`），XTP MD 全套（EventEngine/ThinGateway/GuardedXtpMdApi/XtpMdSession）封在 `XtpMdGateway`（`:133-228`）；SDK 守卫在 `md_api_guard.py:49`（`GuardedXtpMdApi(XtpMdApi)`，`:32-33` 直接 import `PROTOCOL_VT2XTP/XtpMdApi`）。→ 把 `XtpMdGateway`/`GuardedXtpMdApi`/`XtpMdSession` 整体搬进 `providers/xtp/` 即可，行为零改。
  - TD 半边：`td_registry._build_xtp_runtime` 直接 import **非公开** `XtpTdApi`（`:59-60`）并自写 `ThinTdGateway`（`:74-120`），执行面 `adapters.XTPAdapter`（`:230-448`）→ 搬进 `providers/xtp/` 也成立，但依赖内部 API，必须按 §9.4 做 fork 并锁定。
- **hub 进程本身不是插件**：D32 §四把 hub 列在编排层（:49）；hub 是进程/编排，`XtpMdGateway` 才是插件。hub 的通用面（MinuteAggregator/租约/分发/订阅 diff）应保留。
- **P1「零 import vnpy」在现有结构下：否。** 除 P0-2/P0-5 的 8 文件外，hub 主循环还依赖 vnpy 形状的生命周期面：`md_hub/main.py:36-39`（EventEngine 探测）、`:334-341`（`start_ready`）、`:343-349`（`connected` 重连沿）、`:401-404`（`event_engines` 线程存活检查）、`:415-418`（`poll_supervise`）。这些在 D32 的 `MarketDataPort`（:106-109）里**没有对应方法**，所以 hub 编排层无法在「无 vnpy、无 provider 字段」前提下工作。
- **修法**：`MarketDataPort`/`ProviderRuntime` 增 `start_ready() / connected / poll() / event_threads_healthy()`（或合并为 `health()` 返回结构体）；P1a 只定接口，hub 仍走旧实现；P1b 切 hub main 到新接口（见 P1-7）。

### Q2 client_id 号段与全局唯一

- **否。** 现无任何全局分配者：
  - XTP：`broker.py:128-137`，`(tid-1)%98+2`；同账号 tid 相差 98 即撞。
  - EMT：`td_registry.py:164-165`，`(tid-1)%100+20`；相差 100 即撞。
  - hub MD 与 worker TD 共用行参数 `客户号`（`broker.py:151`，默认 1），仅靠「worker 从 2 起」避让，行参数改成 ≥2 即可能撞。
  - `live_task` 允许多任务同账号（`web_api/routes/trading.py:195-201` 无互斥），多 runner 同账号是常态。
- **修法**：插件内不做「计数器」，只做「申请/续租/释放」；
  - 唯一性归属＝**账号级跨进程租约**（Valkey `xtp:slot:{account_id}:{terminal_id}` SETNX EX + 续租；参考 `md_hub/main.py:136-149`）；
  - 账号行的 MD 客户号先占位（插件启动先 reserve MD 号，再分配 TD 号）；
  - 急停执行器若独立进程，同一套申请接口，不得复用 worker 的号；
  - 失败语义：申请不到 → `EX_TEMPFAIL(75)` 退避，禁止 fallback 到公式派生。

### Q3 双轨防双写/双下单；现有表能否承载

- **否。** `trading_account` 只有 `UNIQUE(provider, account_key)`（`migrations/versions/0116_split_external_interface.py:123-124`），`account_key` 可空（`:105-124` 列声明 `nullable=True`）。含义：
  - 同一物理资金账号可以同时以 `('xtp','X')` 和 `('emt_emq','X')` 两行存在且都 enabled → 两个 hub + 两套 worker 对同一券商账户下单；现有约束**不阻止**。
  - 「账号行唯一 provider」是空话：provider 是行内标量，一行只能有一个值；它不表达「同一物理账号只走一套实现」。
  - `live_task.account_id`（`0104`/`0174`）只保证 FK 与「live 不得绑虚拟账户」（`0174:26-45`），不保证实现选择。
  - P1-P5 双轨的切换点是**代码路径**（`build_td_runtime` 已注册→新 / 未注册→stub，`td_registry.py:33-48`），DB 无对应开关 → 无法审计「这条任务走的是旧还是新」。
- **修法**：
  1. `trading_account` 加 `impl TEXT NOT NULL DEFAULT 'legacy' CHECK (impl IN ('legacy','plugin'))`（账号级，不放任务级——放任务级会让同账号两套实现并存，风险更大）；
  2. 物理账号唯一：`CREATE UNIQUE INDEX ... ON trading_account (account_key) WHERE account_key IS NOT NULL AND enabled`（或新增 `physical_account_id`），阻止 xtp/emt 双行；
  3. worker 读 `impl` 决定 builder，并在 `live_task`/心跳导出 `impl` 便于审计；
  4. 同进程禁止新旧两套 runtime 同时构造（P1a 双轨只允许「旧生效、新只读构建产物不建连」）。

### Q4 S7 迁移等价：能否在不动 prod 前提下证明

- **D32 现文字：不可测**（S7 只说「同输入同结果（订单字段/号段/回报逐项对比）」，:314；无 harness、无基线、与 Q2 的新分配器冲突）。
- **可测方案（建议写进任务文件）**：
  1. **SDK 边界录制回放**（不是 adapter 边界）：monkeypatch `vnpy_xtp.gateway.xtp_gateway.XtpTdApi`（已有先例 `server/tests/test_td_registry.py:50-68` 的 patch 手法），recorder 记录 `connect(...)` 位置参数与每次 `OrderRequest` 全字段（symbol/exchange/direction/type/volume/price/offset/reference）。
  2. 同一 fixture 顺序驱动旧路径（`build_td_runtime("xtp")` + `XTPAdapter`）与新插件（`providers.xtp.build`），逐项断言：
     - `connect` 实参（含客户号）；
     - `OrderRequest` 字典相等（含 `reference=t{tid}:e{epoch}:cN`）；
     - 返回的 `client_id` 序列；
     - 回放 `onOrder/onTrade/onAccount/onPosition` 后的 domain 对象元组（orderid/status/direction/traded/price/datetime/reference）；
     - 撤单退化路径（非纯数字→放弃，`adapters.py:339-361`）行为一致；
     - 查询语义：清缓存、稳定窗 2s、超时 partial（`adapters.py:370-448`）——需要把 `time.time/sleep` 注入成可控时钟或把窗参数化，否则测试会 flaky。
  3. **号段等价与 Q2 冲突**：S7 必须区分「legacy-compat 模式（保持 `(tid-1)%98+2`，仅用于等价证明）」与「生产模式（租约分配）」。否则 S7 永远不可能绿。建议 S7 文字改为「同输入下 **legacy 公式模式** 订单字段/回报映射逐项相等；生产模式只断言唯一性与可审计」。
  4. 全程无网络、无真单；MD 半边另加「tick 字段→聚合 bar」等价（当前 S7 漏了 MD）。
  5. 可选 staging 影子：XTP 测试账号双 client_id 各发 1 手限价再撤；只作彩排门，不进 pytest。

### Q5 S4「改数据即生效」缓存失效

- **否（未定义）。** D32 §7.2 只说「即时生效（缓存 TTL）」（:178），§十四 又把「TTL vs 发布事件」列为开放问题（:322）；S4 要求「改一行→行为随之变」（:311）。
- 现成先例与现状：
  - `close_policy` 是**每次读 DB 无缓存**，天然即时（`data_platform/hub_quote.py:144-148`）；
  - A03 路由有 `cfg:version` + `_RELOAD_TTL=2.0` 轮询（`data_platform/routing.py:26-30,220`），但 `system_config` 写入**不 bump**（`web_api/routes/system.py:520-603` 无 bump；只有接口行/路由改动 bump：`mgmt.py:248-249,276-277`）；
  - 多进程现状：hub 15s 轮询 `cfg:version`（`md_hub/main.py:76-110,414`），worker 无轮询。
- 后果：TTL 内不同进程用不同规则（worker 下单校验 vs UI 展示 vs 风控），同一时刻可能一个放行一个拒绝；对涨跌停/申报范围是不可接受的漂移。
- **修法**：S4 落地定义写死为三句：
  1. 写侧：`instrument_spec/market_spec` 任何写（Web/脚本）必须 `bump_config_version()`；
  2. 读侧：worker 进程缓存 `(version, rules)`，每拍（≤2s）比对版本，变化即整体替换（不做增量）；
  3. 兜底：下单路径 `resolve()` 发现版本不可读 → 退回逐次 DB 读（同 `close_policy`），禁「读不到就用旧缓存」；规则缺失 fail-closed，并在 worker 启动预检全部在跑标的（否则 09:30 才发现规则缺、批量拒单）。
  - 测试用「bump + 注入时钟」而非真 TTL 睡眠，避免 flaky。

### Q6 S1/S2 守门可测性、绕过风险

- **技术上可行；按现文字落地会既漏又误伤。**
- 漏（已存在实例）：
  - 动态导入：`hub_worker.py:455` `__import__("vnpy.trader.event", fromlist=["EVENT_TRADE"])`；EMT 侧 `adapters.py:477-485` `importlib.import_module("emt_trader_api")` 是同类形态。
  - PEP 562 re-export：`strategy_framework/__init__.py:17-33` 用字符串表导出 `XTPAdapter`/`Order`/`Position`，扫描 import 语句看不到 vnpy，但 `from src.strategy_framework import XTPAdapter` 会拉起 `adapters.py`。
  - 层测自身盲区：`server/tests/test_layering.py:110-124` 的 quant_common 第三方白名单用 `os.listdir`（**不递归**）且正则只匹配行首 import → 新增 `quant_common/domain/` 子包**完全不被扫描**；函数内 lazy 第三方 import 也漏（对比 `_module_edges` 全文正则，:59-83）。
- 误伤（S2 字面量扫描）：
  - `markets.py:121-155`、`broker.py:75-80`、`td_registry.py:51,136`、`adapters.py:978-983`、`scheduler/tasks.py:1706`（`for provider in ("xtp",...)`）、`md_hub/main.py:340`（日志）全是合法注册/展示字面量。
  - 现存的**真实分支**在应用层：`strategy.py:305` `return self.config.adapter != "xtp"`；`data_platform/perms.py:125,168`（`attr.category ==`）；`strategy_framework/cross_section.py:234-237`；`quant_common/fees.py:49`（`market == "astock"`）。S2 说只扫「编排/端口层」，但 §二目标是「通用层零分支」，口径不一致。
- **修法**：
  1. S1 用 AST：`ast.walk` 收集 `Import/ImportFrom` + `Call(func=Name("__import__")/Attribute(importlib.import_module))` 的字符串常量；同时扫 PEP562 re-export 表的 value（`.adapters`）是否传递可达 vnpy；
  2. 域声明清晰：S1/S2 只扫「新端口/规格/注册表/编排」目录 + `KNOWN_VNPY_IMPORTS` / `KNOWN_PROVIDER_BRANCHES` 冻结基线（`file:line`，只减不增），P3/P4 每批还账；
  3. S2 只禁**分支比较**（`ast.Compare` 中一侧为 provider/category 字面量），允许注册表字面量；allowlist 文件（`markets.py`/`registry.py`）显式列出；
  4. 守门自身突变复测：手动把 `import vnpy` 换成 `__import__`/re-export 形态，守门必须红（历史上守门自失效已出现过，必须钉）。

### Q7 P1 风险：新 domain 与 vnpy/项目 dataclass 并存；回测/paper

- **「P1 不改行为」按现范围：否。**
- 并存现实：
  - 项目已有 `adapters.Order/Position`（`adapters.py:27-45`），`strategy.place_order` 直接构造它们（`strategy.py:496`）；
  - 但交易链真实消费的是 **vnpy 对象**：`trading.write_trade_log` 读 `vt_orderid/direction/datetime`（`:88,131,155`），`reconcile_orders` 读 `Status`（`:514`），`halt_edge_cancel` 用 `od.vt_orderid` 撤单（`:302`）；
  - 所以 domain `Order/Trade/Position` 若要保持行为，要么**字段+枚举都兼容 vnpy**（domain 就得依赖 vnpy → 违反 S1），要么改 `trading.py`（行为面变更）。
- 回测/paper：
  - `BacktestAdapter(ExecutionAdapter)`（`backtest.py:63`）从 `.adapters` 取 `Order/Position` 并实现 `send_order`；`PaperExecutionPort` 替换它意味着改 import 与基类；
  - `paper_mode` 是 `ExecutionAdapter` 的**类级横切标记**（`adapters.py:57-61`），`main.py:401-403` 注入；paper 走的是「provider stub + paper_mode 记账」（`main.py:393-410`、`adapters.py:299-308`），不是独立端口。虚拟账户 `provider='paper'`（`migrations/versions/0127_paper_trade_log.py:55-56`）依赖「未注册 builder → None → stub」（`td_registry.py:39-40`），新 REGISTRY 若无 `paper` 插件，S3 能力协商会直接拒掉 paper 任务。
- **修法**：
  1. P1a 的 domain 对象**不接线**，只作为端口契约存在；P1b 加一层 `providers/*/translate.py`，端口内部把 vnpy→domain，`trading.py` 逐步改（每改一处配行为等价测试）；
  2. 明确 `paper` 是**内置 provider 插件**（`capabilities=["trading"]`，无连接），或 REGISTRY 显式列 `NON_PLUGIN_PROVIDERS={"paper"}` 豁免，别让 S3 误杀；
  3. `paper_mode` 保留为 runtime 属性（跨端口模式），不要塞进某个 Port；`BacktestAdapter` 可结构化满足 Protocol（Python 不强制继承），但 `isinstance` 断言处需清点。

### Q8 §十一 文件清单/范围遗漏

**是，有遗漏。** 至少缺：

| 遗漏对象 | 真实代码 | 为什么必须进表 |
|---|---|---|
| MD 网关插件（XTP/EMQ/加密） | `strategy_framework/md_gateway.py:133-228`（XtpMdGateway）、`:231-...`（EMQ）、`:518-630`（加密） | D32 只写 TD 半边；S1 红点一半在 MD |
| XTP SDK 生命周期守卫 | `strategy_framework/md_api_guard.py:32-33,49` | 依赖 `vnpy_xtp` 内部类，必须随插件搬 |
| 连接窗/会话 | `strategy_framework/md_session.py:134-158`（`load_xtp_window_cfg/xtp_session_window_open`）、`:208+`（`XtpMdSession`） | §9.5 说归插件，但消费点在编排层（`hub_worker.py:393-401`、`td_registry.py:70-72,161-162`、`md_gateway.py:149-161`） |
| hub 主循环的网关生命周期面 | `md_hub/main.py:230-233,334-341,343-349,401-418` | 端口缺 `start_ready/connected/poll/event_threads`，S8 无法成立 |
| 配置 schema 真源 | `data_platform/interfaces/base.py:34-48,58,84-90`；消费 `broker.py:249-258`、`mgmt.py:177-195` | manifest.config_schema 与 FIELD_SCHEMA/PARAMS_SCHEMA 双源，表单与必填校验会分叉 |
| 能力真源 | `quant_common/markets.py:51,121-155`；`data_platform/capabilities.py:16-30`；`adapters/base.py:559` | manifest.capabilities 成第五处 |
| 调度 SA4 期望源 | `scheduler/tasks.py:1875-1881`（`list_md_gateway_providers` 白名单）、`:1706`（provider 字面量） | 插件化后 hub 期望表/健康检查必须同步，否则「不拉起/拉起即 78」 |
| 规则既有存储 | `system_config close_policy.*`（`hub_quote.py:124-179` + `migrations/versions/0175_close_policy_seed.py`）、`band_rules`（`security_master.py:363`）、`MARKETS`（`markets.py:37-45`）、费用规则（`quant_common/fees.py:13-15,49-58`） | 不写「退役/吸收」= P2 制造第三套规则真源，改价/改带行为分叉 |
| 品种/合约规则现状 | `security_master.category/board`（§7.2 提到复用） | 表里没写 P2 的搬迁/优先级实现 |
| paper 虚拟账户 provider | `0127:55-56`；`main.py:393-410` | REGISTRY 无 paper 则 paper 任务起不来 |
| 插件依赖安装模型 | `server/requirements.txt:10-13`（vnpy 单 lock/hash）；`deploy/wrappers/quant-pip-wrapper:75-76,120-128` | §9.2 的 `providers/<id>/requirements.txt` 部署管道不认（单 lock + `--require-hashes`），会静默缺依赖 |
| 规则管理 UI 的权限/导航 | `web_api/routes/mgmt.py`、`perm_registry` nav 18 项、`test_route_auth_gate` | P2「管理面」不是只加表：要加 nav/api/market_op 键 + 路由认证守门 |

---

## 三、P1 清单（须改，不阻断方向）

| # | 问题 | D32 位置 | 代码证据 | 一句话修法 |
|---|---|---|---|---|
| P1-1 | 双轨「同账号单套生效」无机制 | :295,301 | `0116:123-124`；`0174:26-45`；`trading.py` create 路径 | 账号级 `impl` 列 + `account_key` 部分唯一 + 心跳导出 impl |
| P1-2 | S7 不可执行且与号段新分配器冲突 | :314 | `broker.py:128-137`；`test_td_registry.py:50-68` | 写死 golden-master + legacy-compat 号段模式 + 补 MD/查询语义项 |
| P1-3 | S4 缓存失效未定义（TTL≠即时；多进程不同步） | :178,311,322 | `hub_quote.py:144-148`；`routing.py:26-30,220`；`system.py:520-603`；`md_hub/main.py:414` | 写侧 bump `cfg:version` + 读侧 ≤2s 版本轮询 + 读不到回落逐次读 |
| P1-4 | S1/S2 守门可绕过/误伤 | :308-309 | `hub_worker.py:455`；`__init__.py:17-33`；`test_layering.py:110-124`；`strategy.py:305`；`fees.py:49` | AST+动态调用+re-export 扫描；新目录口径+基线棘轮；S2 只禁比较分支 |
| P1-5 | 规则第三真源（不退役 close_policy/MARKETS/band_rules/fees） | :176-178 | `hub_quote.py:124`；`0175`；`markets.py:37-45`；`fees.py:13-15` | P2 任务文件写「吸收+退役+种子映射」三件套，禁并行双写 |
| P1-6 | 迁移清单缺 MD/配置/能力/调度/UI（见 Q8 表） | :280-293 | Q8 各行 | 补表并标批次；每行给「旧→新→守门」 |
| P1-7 | 端口/运行时缺生命周期钩子，S8 无法断言「编排无感知」 | :253,315 | `md_hub/main.py:334-349,401-418`；`hub_worker.py:808-830`；`trading.py:665` | Port 增 `connected/start_ready/poll/health`；worker 删 `ctx["td_window"]/["td_connect"]` |
| P1-8 | 插件独立依赖与单 lock/hash 部署模型冲突 | :242 | `requirements.txt:10-13`；`quant-pip-wrapper:75-76,120-128` | 二选一：依赖仍进全局 lock；或改管道支持 per-provider 分片（须 A06 重审） |
| P1-9 | fail-closed 规则缺失与现 fail-open 默认冲突 | :191 | `hub_quote.py:164-173`（缺行回落缺省+warning） | P2 上线前从现默认/band_rules 全量种子 + worker 启动预检在跑标的 |
| P1-10 | P1 domain 不接线则 S1 红；接线则行为变 | :125,302 | `adapters.py:27-45,57-61`；`backtest.py:63`；`main.py:401-403` | P1a 契约不接线 + P1b 翻译层/事件端口；paper 保留 mode 语义 |
| P1-11 | 同账号手工改 `params.client_id` 可撞 hub MD 号 | :230 | `broker.py:151,199-200` | 插件启动先 reserve 行内 MD 号；写侧校验区间 |
| P1-12 | `vnpy` 版本锁只在 requirements，manifest `version_lock` 无强制消费 | :231 | `requirements.txt:10-13` | 启动校验 manifest.version_lock == 已装版本，不符 fail-fast（或警告+健康降级） |

## 四、P2 清单（可随后批修）

- **命名双义**：manifest `client_id`（客户号，:230）vs `Order.client_id`（委托引用，`adapters.py:34`）。→ manifest 改 `terminal_id` / `session_slot`。
- **规则字段重复**：`MarketSpec.settlement`（:152）与 `InstrumentSpec.t_plus`（:167）；`MARKETS.trading_day_anchor`（`markets.py:31`）又一份。→ 明确 `t_plus` 为准，settlement 只表达资金/交收。
- **双选择器**：D32 `REGISTRY.pick(provider, need)`（:272）vs A03 `RoutingTable.resolve`（`routing.py:243`）。→ 写清「数据侧路由归 A03，执行侧按账号行 provider 直取，pick 只用于多插件能力协商」。
- **S2 口径**：`perms.py:125,168`、`cross_section.py:234-237` 的 category 分支不在编排层，清零要等 P2 规格落地。→ S2 注明分批。
- **S6 与可选依赖**：D32 要求 discover/启动校验 fail-fast（:276），但 EMT SDK/.so 本机可能缺（`adapters.py:477-485` 惰性加载；`interfaces/base.py:71-75` 容错跳过）。→ 改为「单 provider 不可用=该 provider 标记 unavailable，不拖垮进程」。
- **research 能力扩展**：新增第 6 token 要同步 `DOMAIN_CAPS`（`markets.py:152-155`）与「两集互斥并集=CAPABILITIES」守门（`test_markets_registry`），且 research 不属于现有两域，需新增第三域或明确 `DOMAIN_CAPS` 例外。
- **健康检查/wrapper**：`deploy/wrappers/quant-hbcheck` 期望集=enabled 交易行（`0128` 注释），新增 impl/plugin 列需同步该读点，避免彩排阶段 8 红。

## 五、建议的批次切分（改后用）

- **P1a（真·零行为）**：`quant_common/domain`（纯 dataclass/Enum）+ `ports/`（含事件/生命周期方法签名）+ `registry/`（唯一注册表，legacy 投影只读）+ `providers/` 空架 + 守门（只扫新目录 + 存量基线棘轮）。
- **P1b（接线）**：事件端口 + `providers/*/translate` + `trading/hub_worker/main` 切读 domain；每处配「旧 vs 新」等价测试；此批允许行为等价变更但不得改判定语义。
- **P2**：两表 + 管理面 + 吸收/退役 close_policy/MARKETS/band_rules/fees + 版本化失效 + 启动预检。
- **P3**：XTP 插件（MD+TD 同包、号段租约、连接窗内移）+ S7 golden-master 全绿 + impl 开关灰度。
- **P4/P5/P6**：按 D32。

---

## 六、结论

**须改后再审。** 先改五条 P0（注册表收编、事件端口+生命周期、build 角色化、号段跨进程租约、P1 拆分与基线棘轮），并把 §十一 清单补全（MD 半边、schema/能力真源、调度/UI、既有规则存储、paper provider、依赖安装模型）。改完后本架构可作为 P1 立项依据，决策 1/2 本身不需要推翻。
