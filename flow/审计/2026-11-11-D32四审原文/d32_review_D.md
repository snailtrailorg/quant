# D32 接入插件架构 · 红队证伪报告（审官 D，独立盲审）

- 审阅对象：`docs/design/D32-接入插件架构.md`（325 行，2026-11-10 定稿）
- 对照代码：`server/src/strategy_runner/`、`strategy_framework/`、`md_hub/`、`quant_common/markets.py`、`data_platform/`、`server/tests/`、`deploy`/A06
- 方法：假设 D32 已实施一年后出事，倒推失败路径；每条必须落到 D32 具体条目 + 真实代码/文档行号
- 前提遵守：不质疑"vnpy 内化""规则数据化"两项用户裁定，只推演**做了之后怎么失败**

---

## F1 · "动态插拔"在本仓部署模型下不存在，插件升级 = 全量发布 + 全波次重启

**失败场景（可复现）**
P3 之后某天要修 `providers/xtp/` 里一个号段边界 bug。开发者按 D32 §10「新增/升级插件只影响其包」以为只需替换一个包 → 实际路径是：仓库改动 → 新 `release_id` → `release.yml` → 阶段 5（pip+alembic）→ 阶段 6 翻链接 → 阶段 7 **波次重启**（hub 实例 + 所有 `quant-live-task@*`）→ dwell 稳定窗。盘中执行被 `deploy_trading_windows` 闸拦（A06:390），只能等盘后；一次线上小 bug 的最短修复周期 = 1 个交易日。若强行带 `-e force=true`：hub 退出重建期间 `hub:bars:*` 断流，worker 进入盲视（`hub_worker.py:775-783` "盲视状态——BUY 将在下单时刻被拒"），持仓在盲视窗内无法减仓。

**触发条件**：任何插件代码改动；或首次引入 D32:242 的 per-plugin `requirements.txt`。

**根因**
1. D32 §10 把"插件"当部署单元，而本仓唯一部署单元是 **release**（A06:19/165「版本树不可变，所有变更产生新 id；回滚=换链接」）。D32 全文没有定义插件的**交付与安装路径**，`discover()` 只能扫到已经在 release 树里的包。
2. D32:242/261 的 per-plugin 依赖与 `version_lock` 与 A06:159/166 冲突：所有进程 `exec "$ROOT/shared/venv/bin/python"`，**venv 跨 release 共享且"结构上不可指纹"**。把 `vnpy_xtp` 升级进 shared/venv → 旧 release（还留着做回滚、以及其它插件）同时炸；A06:220 的 `rollback-freeze-diff` 只是**漂移报告，不是恢复**——回滚链接之后依赖仍是新的。
3. `discover()` 在 systemd 常驻进程里只在 import 时执行一次，**运行中的 hub/worker 不会重扫**。所谓"加插件重启即出现"，重启的粒度是整个 release 的波次，不是这个包。

**证据**：D32:272-276（discover/热插拔边界）、D32:242、D32:261、D32:231（version_lock）；A06:19、A06:159、A06:165-166、A06:216-220、A06:390。

**修法**
- 删除"热插拔"表述，改写为：**插件是随 release 交付的代码单元；新增/升级插件 = 一次完整发布**。
- 删除 per-plugin `requirements.txt`（单 venv 下不可实现）；依赖变更统一走 `requirements` + `quant-pip-wrapper`，并在 P2 验收里写明"改依赖必须整批重发（含 rollback 窗口）"。
- 若确需真热插拔，须先立项改 A06（per-plugin venv + 独立进程 + 新 wrapper + 新 sudoers 条目），这已超出 D32 的"输入→消费者"范围——D32 现在假装它不需要。
- **接受并监控的边界**：插件升级频率 ≤ 现有发布节奏；每次插件升级必须与"hub+worker 全量重启"绑定评估，盘中禁止。

---

## F2 · `build(account)` 契约缺 mode 与 task 上下文 → hub 建 TD、worker 建 MD、订单号唯一性失去来源（P0）

**失败场景**
P3 按字面实现 `ProviderPlugin.build(account) -> ProviderRuntime`，`ProviderRuntime` 按 §9.3 同时暴露 `execution/market_data/research/account/history` 五个端口。hub 进程（`md_hub/main.py`）为了拿 `market_data` 调 `build(account)` → 插件内一并建了 TD 会话；N 个 worker 为了拿 `execution` 也调 `build(account)` → 每个 worker 各建一套 MD。后果两条同时发生：
- 同账号出现 1 个 hub TD + N 个 worker TD，且都按 manifest 的 `client_id: {mode: range, min:2, max:99}` 自行派生号段（D32:230）→ 撞号。这正是 2026-08-22 的"user already exists"事故族（`broker.py:181-186`：同账号同 client_id 仅一个 MD 会话）。
- worker 侧 MD 抢占 XTP 行情槽位/订阅额度（`md_api_guard.py` 头部记载 2026-08-25 槽位回收事故）。

同时，订单唯一性断链：今天 `client_order_id` 前缀是 `f"t{tid}:e{boot_epoch}:"`（`td_registry.py:131`、`adapters.py:244`），由**编排层持有 task_id 与 boot_epoch**；`0040_bar_hub_shadow.py:43` 的 `ux_order_log_client_oid` 唯一索引正是靠这个前缀防"多 worker 重启同分钟撞 id"。插件只拿 `account`，生成不出 tid/epoch → 要么撞唯一索引（下单前 INSERT 失败 → 交易链断），要么为绕开而削弱前缀 → 成交归属错认。

**触发条件**：P3 首次实现 `build(account)`；hub 与 worker 都走同一入口（D32:250-256 只给了这一个入口）。

**根因**
- D32 把"进程拆分（hub=MD only / worker=TD only）"错放进 transport（D32:261「TD-only 拆分封在 `_transport.py`」）。进程语义是**调用方模式**，不是传输层细节；`build()` 签名里没有 mode，插件无从知道该建哪半边。
- D32:296（S8）要求"编排层无感知连接窗/号段"、D32:314（S7）要求"号段逐项对比"——但号段的输入（tid）被契约排除，**S7 在 D32 自身契约下不可满足**（内部自相矛盾）。

**证据**：D32:250-256、D32:261、D32:296、D32:314；`md_hub/main.py:4`（"纯数据面…零 TD 会话"）、`td_registry.py:3-8`（"hub 零 TD，worker 零 MD"）、`td_registry.py:130-131`、`adapters.py:244`、`server/migrations/versions/0040_bar_hub_shadow.py:43`、`broker.py:128-137`。

**修法**
- 契约改为 `build(account, mode: Literal["md","td","full"], task_ctx: TaskCtx|None) -> ProviderRuntime`，`ProviderRuntime` 按 mode **只暴露合法端口**，越界访问抛 `UnsupportedCapability`（与 D32:124 同哲学）。
- 订单号前缀仍由编排层生成（它有 tid/epoch），插件只保证"收到 cid 就照用"；或 `task_ctx` 必须携带 `{tid, boot_epoch}` 且 manifest 声明 `order_id_policy`。
- S7/S8 的验收断言需重写：S8 断言"插件不持有 window 配置"，不能断言"编排层无字段"（编排层必然持有 tid/epoch/role）。

---

## F3 · 规则 fail-closed 落到"平仓/减仓"路径 = 把可平仓变成不可平仓（P0，资损级）

**失败场景**
急停全平（`trading._close_all_positions`）每轮对每个持仓调 `rules.validate()`+`reference_price()`。当日刚上市的一只可转债（`security_master` 有行但 `instrument_spec` 生成链尚未落行），或 PG 抖动导致 spec 读失败 → D32:191「规则缺失 → fail-closed 明确报错」→ `validate` 抛错 → 该标的进入 `no_quote/skip` 分支（`trading.py:564-575` 的 skip 语义）→ **持仓平不掉**，`systemic_down` 升级告警后转人工。而今天的同一条路径是 fail-open：`hub_quote.get_close_policy` 读不到/值非法时回落到代码缺省（`hub_quote.py:124-152` + `_CLOSE_POLICY_DEFAULTS:77-83`），急停照样按跌停价挂单，只在日志留一条 warning。

**触发条件**
- 新品种/新板块当日上市（spec 表滞后于 `security_master`）；
- spec 表读失败（DB 抖动/连接池耗尽——恰好是急停时刻的高并发场景）；
- 运营删错一行/`effective_from` 写成未来时间。

**根因**
1. D32:191 把"规则不存在"与"规则读不到"合并成一个 fail-closed 语义。
2. D32 未区分**开仓**（可拒，代价是错过交易）与**平仓/reduce-only**（不可拒，代价是持仓暴露）。`RuleSet.validate` 是一个统一入口（D32:184），没有 `intent.side`/`reduce_only` 分流。
3. 与同一条下单链上的另一套规则缓存语义相反：风控 `_maybe_reload_rules` 是 60s TTL **失败沿用旧规则（fail-open）**（`risk.py:287-288`）。同一笔单先过 fail-open 的风控、再过 fail-closed 的 RuleSet，谁赢没有定义。

**证据**：D32:184-191；`hub_quote.py:59-83`（缺省/类型/合法值）、`hub_quote.py:124-152`（读失败回落+warning）；`trading.py:495-575`（急停每轮重取价、skip 分支）；`risk.py:287-288`；`migrations/versions/0175_close_policy_seed.py`。

**修法**
- 语义分层写进 §7.3：①**开仓** 规则缺失 → fail-closed 拒单并在 UI 标"规则未配置"；②**平仓/减仓（reduce_only 或 `reason=emergency_close_all`）** 规则缺失 → fail-**open** 到"仅用行情可得的硬约束"（`tick.lower_limit`/对手价，即现行为）+ 高优告警；③**读失败 ≠ 行缺失**：前者降级 fail-open 并告警（与 `close_policy` 现行为一致），后者按 ①②分流。
- `RuleSet` 增加 `mode: open|reduce` 参数，`validate` 内分流；S4 断言补两条：缺行时开仓必拒、平仓必放（真库行为断言）。
- **接受并监控的边界**：平仓 fail-open 可能产生交易所拒单（`风险：废单`）——可接受，急停已有重试循环与拒单回执比对（`trading.py:657-658`）。

---

## F4 · 声明式规则的表达力悬崖：动态约束只能回流成"插件里一堆 if"，且与三处既有规则源打架（P1，其中双源一条为 P0）

**失败场景（表达不了的三类，全部来自真实代码）**
1. **可转债临停**：单次涨跌 20%/30% 触发临停 30 分钟、14:57 复牌——需要在盘中维持"该标的是否处于临停"的**状态机**，并在临停窗内拒单/暂缓。`InstrumentSpec.venue_flags: {convertible_halt}`（D32:172）只是一个静态 flag，表达不了"何时进入/退出"。
2. **有效申报范围**：申报价须在"最新成交价 ±2%"（且不超涨跌停、市价申报另有口径）。`price_range: {mode: venue|pct|none, lower, upper}`（D32:171）是静态上下界，无法表达"以运行时最优价为基准的百分比带"。真正需要的是 `f(tick) -> (lo, hi)`。
3. **可卖量**：`sellable_rule: volume_minus_frozen`（D32:168）假设 frozen 语义统一；实际 frozen 来自券商回报且含在途卖单冻结（`trading.py:561-575` 的 `sellable = vol - frozen` 注释、`adapters.py:44`），不同柜台口径不同——这是**数据+口径**耦合，不是一行枚举能表达的。

**这些逻辑会回流到哪里**：`RuleSet` 只有 4 个固定方法（D32:183-188），没有算子注册位；`derivative_flags`（D32:299）只给期货占位、不给"由 tick 派生的动态规则"占位。于是运营提的第 4、5、6 个需求只能写进 `providers/xtp/plugin.py` 的 `if category == "convertible"`——**声明式变成表面，实际规则散回插件**，且违反 D32:62 的"应用/编排不 import 第三方"精神（插件现在是唯一有 if 的地方，等于把规则层从 `RuleSet` 挪进 `providers/`）。

**双源冲突（P0 级）**：D32:177 说"`band_rules(exchange×board)` 直接并入，不另起炉灶"、D32:289 说"`close_*` 定价族 → RuleSet"，但**没有写退役计划**：
- `system_config` 的 `close_policy.xtp.*`（0175 已 seed 5 键：`has_limit_band/opponent_depth/aggression/max_order_qty/price_tick`）会被生产持续读取（`adapters.py:132-135`），而运维按 D32 去改 `instrument_spec` → 两边都"有值"，生产读 A、UI 显示 B。S4（D32:311）只断言 instrument_spec 改一行行为变，**不会发现生产其实没读它**（若 P2"规格表上线先不强制消费"阶段遗留）。
- `band_rules` + `security_master.st`（时变，`security_master.py:108`）与 `InstrumentSpec.price_band` 同为涨跌停真源。

**证据**：D32:161-173、D32:183-191、D32:299、D32:177、D32:289、D32:311；`adapters.py:126-218`、`hub_quote.py:59-83`、`security_master.py:108`、`security_master.py:356-366`、`migrations/versions/0175_close_policy_seed.py`、`trading.py:561-575`。

**修法**
- 承认三层并写入 §7：**静态 spec（数据）/ 动态约束（代码，按 category 注册的 `RuleSet` 子类）/ 交易所态（插件事件流）**。§7.3 允许"`RuleSet` 子类内出现 category 分支"，把禁止面精确到"编排层与端口层"（与 S2 的 AST 扫描范围一致，别把插件也扫进去）。
- 增加 `RuleSet` 扩展点：`register_rule(category, predicate)` 或 `dynamic_constraints: list[Callable[[tick, intent], Range]]`，让动态约束有家可归，而不是流进插件。
- P2 验收必须含**旧源退役清单**：`close_policy.*` 5 键 → `instrument_spec` 的字段映射表 + 删键迁移 + 读侧切换断言（"改新表 → 生产行为变" + "旧键已不存在"）；`band_rules` 同理（要么并入 `instrument_spec` 并删表，要么在 D32 里明写"涨跌停仍归 `security_master`，`price_band` 只引用不存值"）。
- **接受并监控的边界**：期货/期权规则确实不在首批（D32:24/299 已声明），但 **可转债临停与有效申报范围在本仓是已上市品种**，不能挂"非目标"。

---

## F5 · 迁移期"同账号只允许一套生效"在技术上无强制，最坏是双下单（P0）

**失败场景**
P3 为"可回滚"加一个实现选择开关（env 或 DB 列）。A06 允许多个 release 树并存、被钉进程按 cwd 各跑各的（A06:154-162）；运维为回滚把 `server` 链接拨回旧版但没有 `systemctl restart` 全部波次（或旧单元被手工 `start`）→ 同一账号同时存在：
- 旧路径 `quant-live-task@{tid}` 持 XTPAdapter（`_build_xtp_runtime`），新插件路径另一个 tid 持新 `ExecutionPort`；
- 两套号段派生器（`runner_client_id(tid)=(tid-1)%98+2` vs manifest range 2..99）在同一账号内不同 client_id → **两个 TD 会话都被交易所接受**（XTP 只拒"同 client_id 重复登录"，不拒同账号多会话，`broker.py:181-186`、`td_registry.py:66`）；
- 两条链各自收到同一策略信号 → 同一标的被下两次单；`ux_order_log_client_oid` 拦不住（前缀含不同 tid）。

**触发条件**：P3/P4 期间存在两套实现（必然存在，D32:295 自称"双轨并存"），且任一次发布/回滚/人工操作使两套同时被拉起。

**根因**
- D32:301 把进程级唯一性寄托在数据上：「同账号单套生效，由 `REGISTRY` + 账号行唯一 provider 保证」。`REGISTRY` 是 **provider→plugin** 映射，账号行的 `provider='xtp'` 无法区分"旧路径 vs 新插件"——**没有"实现版本"这个维度**。
- 也没有跨进程互斥原语。今天真正防撞号的是 client_id 号段（"tid 派生唯一性"，`td_registry.py:143`），而不是"账号行唯一"。

**证据**：D32:295、D32:301、D32:230、D32:232；`broker.py:128-137`、`broker.py:181-186`、`td_registry.py:63`/`:143`/`:165`、`migrations/versions/0040_bar_hub_shadow.py:43`；A06:154-162（多 release 并存 + `/proc/<pid>/cwd` 钉版本）。

**修法**
- 账号行增加 `impl_version`（或 `runner_impl`）枚举列，与 `provider` 正交；`REGISTRY.pick(provider, need, impl)` 双键。
- 进程启动即取 **per-(account, role) 互斥租约**（Valkey，与 hub 现有 lease/gen 同款，`md_hub/parts.py` 的 `_LEASE_RENEW_LUA`）——拿不到即 exit 78 并告警，不靠"人不会同时起两个"。
- P3 禁止运行时开关，只做**整发布切换**：同一 release 内只允许一套实现（旧路径删除与新路径上线在同一个 release），回滚 = 整 release 回滚（A06 原生能力）。这比"逐 provider 可回滚"（D32:295）诚实且可实现——D32 允诺的 provider 级回滚在当前部署模型下不存在（见 F1）。
- S7 增加行为断言：**同账号第二实例启动必被拒**（不是"号段不同所以能起"）。

---

## F6 · 组合器把"每单解析"和"建连一次"混成一段伪代码 → 急停时 N×M 次 DB 读 + 三进程缓存分裂（P1）

**失败场景**
D32:76-89 的 `resolve()` 里同时有 `MarketSpec.of()`、`InstrumentSpec.of()`、`prov.build(account)`，而 `place()` 每单调 `resolve()`。若照字面实现：
- 急停全平 200 个持仓 × 最多 10 轮（`retry_max` 缺省 10）= 6000 次 spec 解析，全部落在"市场正在崩、连接池最紧张"的时刻。今天同一路径每单只读一次 `close_policy`（1 次 `system_config` 查询），且代码里专门注释"避免同轮重复 DB 读"（`adapters.py:160`）。
- 三进程各自 TTL 缓存（web 配置面 / hub / 每个 worker）：运营 11:29 把 `tick_size` 从 0.001 改成 0.01，web 立刻显示新值，worker 的 TTL 还有 30s → 期间挂单价被取整到旧 tick → 交易所拒单或价格越界（可转债/ETF 的 tick 不同档位）。
- D32:322 把缓存失效（TTL vs 发布事件）列为**开放问题**——这等于把资损相关的语义留到实施期决定。

**触发条件**：急停高峰期；或运营在盘中改 spec（D32:178「运营可改、即时生效（缓存 TTL）」明确鼓励盘中改）。

**根因**
- 把两套生命周期不同的东西写进一个函数：**规则是热的**（每单读、可改、TTL 秒级），**连接是冷的**（进程启动建、改则重启，`md_hub/main.py:246-250` 的 `exit(9)` + systemd 拉起就是现行"重连/换账号"机制）。伪代码让"建连"看起来像每单调用的廉价操作。
- 没有"同一笔单内规则快照"概念：validate 与 reference_price 可能读到两个版本的 spec（中间运维保存）。

**证据**：D32:76-89、D32:178、D32:322；`adapters.py:155-195`（`close_policy` 被 `has_limit_band`/`reference_price`/`round_close_price` 各读一次，靠传 `policy` 参数省一次）、`adapters.py:160`、`hub_quote.py:124-152`、`md_hub/main.py:76-110`（`CFG_VERSION_KEY` 版本号先例）、`risk.py:287-288`（另一套 60s TTL）。

**修法**
- 伪代码拆成两个函数并写清生命周期：`resolve_rules(account, symbol, need) -> RuleSet`（进程内 LRU + 全局 `cfg_version` 键，命中即返回快照对象）与 `get_runtime(account, mode)`（进程启动一次，绑定到编排层）。
- 一笔单内**冻结快照**：`place()` 入口取一次 `RuleSet`，validate/reference_price/sellable 全部消费同一对象（D32 自己也强调"只算一次"哲学——171s 的 `close_action` 只算一次，同理适用于规则版本）。
- 失效机制定为**版本号 + 懒加载**（复用 `CFG_VERSION_KEY`），TTL 只做兜底；D32:322 的开放问题在 P2 前必须关闭，不能带进实施。

---

## F7 · 研究域并入同一注册表 → 能力集守门结构性破裂 + 告警/DB 面污染（P1）

**失败场景**
P6 给研究域加第 6 个能力 token `research`。D32:127 要求"同步扩 `CAPABILITIES` 与 `KIND_CAP_CLASS`（两集互斥且并集=CAPABILITIES 守门一并改）"。但守门实际断言是：
- `set(CAPABILITIES) == {"hist_quote","rt_quote","trading","ref_data","inst_event"}`（`test_markets_registry.py:15`）
- `set(KIND_CAP_CLASS.values()) == set(CAPABILITIES)`（`:20`，**每个 token 必须由某个 DataKind 产出**）
- `set(KIND_CAP_CLASS) == set(DATA_KINDS)`（`:19`）

而 D32:41 明确"研究域**不是** DataKind"。于是只有两条路：**(a) 弱化 :20 这条守门**（把"每个 token 非空"改成"允许空"——正是 31 项漏网事故后被补上的断言），或 **(b) 伪造一个 `research_doc` DataKind**（把研究文档拖进 A03 同步池/数据管理，D32:41/§3 明令禁止）。两条都是"守门自失效"的教科书形态。

**次要污染**：研究抓取失败若与交易共用健康/告警面（D32:37 横向共享"健康·审计"）：`health_monitor` 的 R4 期望集按 `list_md_gateway_providers()` 扫 `trading_account`（`monitor.py:96-110`），研究 provider 一旦进同一枚举就进入 hub 缺席判定/波次白名单；抓取失败若按 critical 出页，会稀释真正的交易告警。

**触发条件**：P6 落地；或更早——只要有人按 D32:127 提前把 token 加进 `CAPABILITIES`。

**根因**：D32 把 `CAPABILITIES` 当成通用"能力枚举"，但它在代码里已被立法为**"DataKind 的派生归类视图"**（`markets.py:13-16` 注释："能力真源=DataKind(27 词)；能力集=派生归类视图，非独立建模"）。研究域是**端口能力**（`ResearchPort`），不是数据能力；两者混用一个词表必然撞墙。

**证据**：D32:37、D32:41、D32:127、D32:197、D32:316；`markets.py:34-39`、`markets.py:51`、`markets.py:61-80`、`markets.py:119-137`；`server/tests/test_markets_registry.py:15`/`:19`/`:20`；`server/src/health_monitor/monitor.py:96-110`。

**修法**
- 两套词表分开：`CAPABILITIES`（数据域，与 DataKind 双向绑定）**不动**；新增 `PORT_CAPS = {execution, market_data, research, history, account}` 写进 manifest（D32:225 的 `capabilities` 其实是端口能力，应改名 `ports` 或 `port_caps`，避免与数据能力同名）。
- 研究域 provider 用独立 registry namespace（`research_providers/`），健康分级独立：`research.*` 告警**不得**进入 `critical` 交易页；事件写 `health_event` 时带 `domain='research'` 供面板分域。
- **接受并监控的边界**：研究域可复用凭证加密/审计基础设施（同一 wrapper/同一张表的不同行亦可），但**不共享告警等级与缺席判定**。

---

## F8 · 守门时序自相矛盾：S1（零 vnpy）与 S7（新旧等价）不能在同一 release 内同时成立，迁移期最危险阶段恰好无守门（P1）

**失败场景**
P1「只搭骨架、双轨并存、不改行为」（D32:295）——此时 `server/src` 里 `vnpy` 仍在 `strategy_framework`（`md_gateway.py` 8 处、`adapters.py` 10 处、`md_api_guard.py:32-33`、`td_registry.py:59-60`、`trading.py` 5 处），S1（D32:308）**必红**。为了让 CI 绿，只能：①把 S1 写成"软告警/白名单" → 守门失效；②等到 P5 结束才启用 → 整个 P3/P4 双轨期（正是双下单/撞号风险最高时）零守门；③先删旧路径再升级 → S7（D32:314 新旧同输入同结果）失去对照物，且回滚语义变成整发布。

**同时**：`md_api_guard.GuardedXtpMdApi(XtpMdApi)` 是**子类化第三方内部类 + 依赖 `PROTOCOL_VT2XTP` 内部常量 + 心跳调用时序**（SEGV 事故防御，`md_api_guard.py:1-30`）。它必须整体搬进 `providers/xtp/`；D32:261 建议"自维护 fork"，但 fork 装进 **shared venv**（跨 release、跨 staging/prod 共享）→ 一次 fork 升级同时改变已发布的旧 release 行为，且 XTP 柜台协议变更时 fork 未跟进 = 登录失败（无降级路径）。

**触发条件**：P3 开始；每次 `vnpy_xtp` 或 fork 变更。

**根因**
- D32 把 S1 当成"最终态守门"，没定义**迁移期守门**（例如"只允许 `strategy_framework/{md_gateway,adapters,md_api_guard}.py` 三个文件 import vnpy，白名单反向校验 + 未列入即红"）——这正是本仓既有做法（`test_layering.py:29-33` 的 `EXEMPT_UPWARD`、白名单反向校验），D32 未沿用。
- S7 与 S1 在同一 release 内互斥：等价性对比需要旧路径活着，零泄漏要求旧路径死了。D32 未说明 S7 跑在**独立测试夹具**（影子对比）而非生产 release。

**证据**：D32:295、D32:308、D32:313-315、D32:261；`md_api_guard.py:32-33`、`md_gateway.py:36/144-146/592/615`、`adapters.py:225/258/309-310/679-680/904`、`td_registry.py:59-60`、`strategy_runner/trading.py:81/296/514/780`；`server/tests/test_layering.py:29-33`（白名单 + 反向校验范式）；A06:159/166/220（shared venv、不可指纹、漂移仅报告）。

**修法**
- 在 P1 就落**迁移期守门**：`tests/test_no_vnpy_outside_providers.py` 用**显式文件白名单**（当前 5 个文件），新文件未列入即红；白名单随 P3/P4/P5 只减不增（与 `EXEMPT_UPWARD` 同范式）。P5 完成后白名单清空，S1 自然成立。
- S7 改为**影子对比夹具**：同一 tick/订单输入分别喂旧 adapter 与新 port（在测试进程内），不要求生产 release 双轨。
- 建立**依赖变更单一入口**：`vnpy_xtp` 及 fork 的版本由 `requirements` 统一钉，插件 manifest 的 `version_lock` 只能是"断言当前装的是这个版本"（启动校验），不能是"per-plugin 安装"。
- **接受并监控的边界**：fork 化不可避免（`XtpTdApi`/`PROTOCOL_VT2XTP` 非公开）；必须写"fork 基线提交 + 每季度对照上游 diff"的运维条目，否则下次柜台变更必炸，且没有回退到官方 API 的路径。

---

## 分级

| 级别 | 条目 | 判据 |
|---|---|---|
| **P0** | F1 热插拔不存在 / 插件升级=全量发布+venv 跨 release 共享 | 停机窗口与回滚承诺失真；依赖升级会同时炸已发布版本，回滚不可恢复 |
| **P0** | F2 `build(account)` 缺 mode/tid | 直接导致撞号/同账号多 TD 会话/订单号唯一性断链 → 双下单与成交错认 |
| **P0** | F3 规则 fail-closed 覆盖平仓路径 | 急停时"规则未配置"= 持仓平不掉 → 直接资损 |
| **P0** | F5 单套生效无技术强制 | P3/P4 双轨期随时可双下单 |
| **P0** | F4-双源（`close_policy.*`/`band_rules` 未定退役） | 运营改新表、生产读旧表 → 定价/取整按旧值，急停价错 |
| P1 | F4 表达力悬崖 + 动态规则回流插件 | 声明式名不副实，规则再次散落（长期债，非当日资损） |
| P1 | F6 resolve 混生命周期 + 三进程缓存分裂 | 高峰期 DB 放大、盘中改规则短暂不一致 |
| P1 | F7 研究域共用能力集词表 | 守门结构性破裂（弱化或伪造 DataKind 二选一） |
| P1 | F8 守门时序矛盾 + fork/venv 漂移 | 迁移期（最高风险）无守门；fork 升级无降级路径 |
| P2 | 研究域告警/DB 污染 | 告警疲劳、PG 负载（可在 P6 前隔离解决，不阻塞 P1-P5） |

## 结论（二值）

**须改后再审。**

架构方向（四维正交 + 端口 + 声明式规格 + 插件）成立，两项用户裁定不需要推翻。但当前文本有 **5 项 P0 属于契约级缺陷**，且全部可在**写 P1 代码之前**在文档里修掉，代价最低：

1. 删除/改写"热插拔"承诺，明确定义插件=随 release 交付（F1）；
2. `build()` 契约补 `mode` + `task_ctx`，`ProviderRuntime` 按 mode 暴露端口（F2）；
3. §7.3 规则缺失语义按"开仓 fail-closed / 平仓 fail-open / 读失败降级"三分（F3）；
4. `resolve()` 拆成 rules 与 runtime 两函数 + 单笔快照 + 版本号失效（F6 的修法，同时解 F4 双源的一部分）；
5. P2 验收补旧规则源退役清单（`close_policy.*` 5 键、`band_rules`），并加入 `impl_version` + per-(account,role) 互斥租约（F4/F5）；
6. 能力集拆成 `CAPABILITIES`（数据域）与 `PORT_CAPS`（端口域），S1 改迁移期白名单守门（F7/F8）。

改完这 6 处，D32 可进入 P1 立项；否则 P1 就会把上述洞固化成代码契约（`build(account)` 签名与端口集合一旦被 5 个插件实现，再改是全线破坏性变更）。

---

### 附：本报告未覆盖（留给其他审官，避免重复）
- 规则 DSL/表达式方案选型（本报告只判"必须留动态扩展位"，不选型）
- 研究域 P6 的解析/向量/成本（D32 已明确另立项）
- Web 管理面（market_spec/instrument_spec 编辑器的 RBAC/审计）未展开
