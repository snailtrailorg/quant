# D32 接入插件架构 · 架构正确性盲审 A

- 审阅对象：`docs/design/D32-接入插件架构.md`（325 行，2026-11-10 定稿）
- 审阅方式：只读；D32 全文 + 对照真实代码/现行文档（未改任何文件）
- 审阅人：general-purpose-1（独立盲审 A）
- 结论：**须改后再审**（P0 × 8，P1 × 14，P2 × 4）

判定口径：P0=架构性错误（照此立项会造出第二真源/断掉现行链路/守门失效）；P1=落地会踩坑；P2=完善。

---

## 一、七问逐条回答

### Q1 四维是否覆盖账户权限、可用资金/保证金、停牌/临停、盘后固定价、结算与交割、行情数据授权？

**否。6 项中仅 2 项有名义位置，4 项无归属或位置错误。**

| 约束 | D32 落点 | 真实承载 | 判定 |
|---|---|---|---|
| 账户权限（品种/板块/ST/可转债权限） | 无（四维全无；AccountPort 只有 credentials/health） | `perms.account_allows`（`data_platform/perms.py:84-166`）、`risk.check_order` 第 2.5/2.6 步（`risk_control/risk.py:320-352`）、A05 §三 | **P0-1** |
| 可用资金/保证金 | Risk 维度仅写「仓位/回撤/频次/熔断」（D32:71） | 资金只在选量时用 `available_cash`（`strategy_framework/strategy.py:331-344`），`check_order` 无现金硬门（`risk.py:251-397`）；保证金仅 crypto 杠杆上限（`risk.py:426-438`） | P1-1 |
| 停牌/临停 | `venue_flags.convertible_halt` 静态布尔（D32:172）；`validate` 列表无停牌（D32:184） | 停牌仅是 DataKind `suspend`（`quant_common/contract.py:35`）+ 日频表；tick 契约无状态字段（`md_gateway.py:8-20`） | **P0-7** |
| 盘后固定价 | `MarketSpec.auction_phases` + `venue_flags.after_hours_fixed`（D32:152,172） | `market_hours.sessions` 已有 `post_fix` 段与 scope（迁移 `0092:89`），但无下单路径消费；RuleSet.validate 不含 phase | P1-9 |
| 结算与交割 | `MarketSpec.settlement: T+0/T+1/T+2`（D32:153） | `security_master.trade_phase`（`security_master.py:70`，迁移 `0092:111/121/131`）已是 T+N 真源；交割 non-goal（D32:24） | P1-9 |
| 行情数据授权 | 无（D32 §三「横向共享」37 行也无） | 仅 D26:65 提到「L2 授权费 per 账号×N」，无 entitlement 模型/代码；订阅侧无门 | **P0-1** |

修法：四维不是完备集。要么加第 5 维 **Account**（entitlements + cash/margin + data_license + halt_state），要么在 §五表下逐项写明「归属文档 + 消费点 + fail-closed 语义」；不允许留空。

---

### Q2 `InstrumentSpec` 能否表达期货平今平赎、组合保证金、期权限价范围、可转债临停？「扩展位」是否等于藏复杂度？

**否；且扩展位就是把复杂度搬回代码，与决策 2 直接冲突。**

- **平今平赎**：D32:160-173 无 `offset`/`close_today`/`close_yesterday` 字段；现行 adapter 恒发 `Offset.NONE`（`strategy_framework/adapters.py:324`、EMT 同 `:691`），平今/平昨语义从未建模 → 不能。
- **组合保证金**：保证金是**账户×组合×跨合约**的净额函数，不是单合约行；InstrumentSpec 是单标的声明，RuleSet 也是 `(account, symbol)` 维度 → 结构性不能。
- **期权限价范围**：`price_range {mode: venue|pct|none, lower, upper}`（D32:171）只能存常量；期权限价依赖标的价、行权价、交易所公式与时段 → 不能。
- **可转债临停**：`venue_flags.convertible_halt` 是布尔，无法表达 ±20%/±30% 触发、时间点、14:57 复牌 → 不能。
- **扩展位**：D32:299 `derivative_flags` + 「插件子规则扩展位」——子规则一旦在插件里就是代码，改规则要发版，直接违反 D32:10 决策 2，并会让 S4「改一行数据行为随之变」永久不可达。

修法：把「首批不含衍生品」写成显式能力边界（D32:24 已有 non-goal），删除「插件子规则扩展位」措辞；衍生品要求进入时，用**新的规则形态**（公式声明/交易所参数表）立项，不得以 `derivative_flags` 夹带代码。

---

### Q3 `RuleSet` 是否能承载「有效申报范围 = 品种 × 交易所」跨维合成，还是仍要写 `if`？

**否。跨维合成缺了「交易所」这一侧的全部输入，实现时必然回落到查表分支。**

- D32:190 声称「品种 `price_range` × 交易所实际规则」，但 `MarketSpec`（D32:146-156）只有 sessions/auction/settlement/currency/channels，**没有任何交易所/板块级价格范围字段**；`InstrumentSpec.price_range`（D32:171）只有 `lower/upper` 常量。
- 现行 `band_rules` 只有 `pct_normal/pct_st`（迁移 `0092:72-77`），`market_hours` 只有 phase/scope（`0092:81-91`）；全仓搜索无「申报范围/价格笼子」实现（仅 D32 自己提到）。
- 结论：合成公式缺右操作数。落地只能 `if exchange==... / if board==...` 或偷偷查 `band_rules`，S2 零分支守门（D32:309）会在规则层自撞。

修法：在 `MarketSpec` 增加 `order_price_range`（维度 `exchange[:board] × phase`，模式 `pct_of_ref|abs|venue`，含上下限/参考价），并写明 RuleSet 解析顺序（品种覆盖 > 交易所板块 > 市场缺省）与缺配置 fail-closed。

---

### Q4 分层是否有环/反向？`ProviderPlugin` 与三端口边界是否清晰？

**依赖方向本身没有成环，但有三处边界错误 + 守门落不了地（否）。**

1. **组合器无层**：`resolve()`（D32:76-88）同时 import 注册表、规格、插件 build，却不属于 §四 任何一层；它返回 `driver`（插件实体）并让应用 `driver.send(...)`，**绕过端口**——与 D32:48「应用只依赖 Ports + Registry」自相矛盾（P1-3）。
2. **端口/注册表物理位置未定，与 `quant_common` 纯度冲突**：`test_layering.py:12-35` 规定 `quant_common` 不得 import 任何 `src.*`；若端口/注册表放 `quant_common`，`discover()` 必须 import `src.providers.*` → 铁律测试必红。且 `providers` 不在 LAYER 表，D32 §四 的依赖规则**没有守门载体**（P1-3）。
3. **插件实体与端口能力混用**：`ProviderRuntime` 一次性聚合 execution/market_data/research/account/history（D32:248-256），而现行拓扑要求 hub 进程只有 MD、worker 进程只有 TD（D26:56-62；`md_gateway.py:42-46` ThinGateway 的 send_order 直接 raise；`td_registry.py:126` 只连 TD）→ **P0-4**。
4. **端口缺生命周期面**：`MarketDataPort`（D32:106-109）只有 subscribe/snapshot/stream，缺 hub 主循环依赖的 `start_ready/connected/poll_supervise/set_on_tick`（`md_gateway.py:78-115`）；`ExecutionPort`（D32:96-104）缺 `connect/close/connected`（`main.py:534`、`hub_worker.py:794-815`）→ **P0-6**。

修法：`resolve()` 归编排层并只返回端口元组 + RuleSet（runtime 缓存归编排）；`providers` 入 LAYER；端口/规格放 `quant_common`（纯）或 `data_platform`（可读表）二选一并写明；`ProviderPlugin` 只做「实体/工厂」，端口只做「能力」，两者不互相替代。

---

### Q5 三域边界是否真不重叠（D32 执行+实时+研究 / A03 数据 / D26 hub）？

**文字分工清楚，但落地有 3 处「两边都想要」和 1 处「两真源」，判否。**

- **连接/凭证/健康**：D32 §9.5 说连接窗/重连/号段全在插件（D32:264-268），D26:58-62 说连接生命周期=hub 内部状态机，D32:116-118 又给 `AccountPort.credentials()/health()` → 三方重叠（P1-4）。
- **市场制度**：D32 §7.1 新建 `market_spec` 表，而现行 `MARKETS`（`quant_common/markets.py:21-32`）明确注释「MarketSpec…骨架层；market_hours 表=运营真源不变」，且 `market_hours` 已实现 sessions/anchor/calendar/scope（`security_master.py:235-366`）→ **两真源 P0-2**。
- **实时行情**：D32 声明实时归 D26 hub，但 `MarketDataPort` 又对上层暴露 `stream()`（D32:109）；谁消费（hub 内适配器 vs 应用直连）未写，存在绕过 hub 拓扑直连插件的数据面（P1-4）。
- **研究域**：与 A03/D26 不重叠（D32:193-217），但 provider `tushare` 同时是数据源 provider，能力集/域归属未定（P1-6）。

修法：加一张「单一归属表」（凭证读=A02/数据层；连接=插件；进程/心跳/变更检测=hub；健康聚合=hub 心跳；市场制度=market_hours 扩展），并声明 `MarketDataPort` 仅 hub 进程内使用。

---

### Q6 §6.6 收敛是否丢能力（MD/TD 同凭证、一账号一 hub、能力集 5+1）？

**部分丢能力，判否（是「丢」，不是「等价收敛」）。**

- **Broker→ExecutionPort**：Broker 的 `get_credentials()` 解密与 `test_connection()` 配置时校验（`broker.py:29-51`）没有端口落点；配置页「连接测试」走 `_REGISTRY.get(provider).test_connection()`（`web_api/routes/mgmt.py:317-327`），D32 manifest 的 `config_schema` 可补表单但**未写测试动作**（P1-5）。
- **DataSource→MarketDataPort+HistoryPort**：DataSource 还承担限速策略/用量记账/熔断参数/连接测试（`data_source.py:26-113`），两个端口只有 `fetch/subscribe`。A03 §六虽保留限速四层，但 §6.6 的表述会让实施者删掉 DataSource 的 pacing/usage（P1-5）。
- **MD/TD 同凭证**：单 `ProviderRuntime` 反而**无法表达**「同一行凭证，hub 开 MD、worker 开 TD，两进程」——与 D26 核心不变式冲突（**P0-4**）。
- **一账号一 hub**：D32 未复述，D26 保留，可接受；但 D32:295/301「同账号只允许一套生效」与「一账号 = 一 hub + 每任务一 TD 会话」直接矛盾（**P0-5**）。
- **能力集 5+1**：`research` 无 DataKind、未归入 `DOMAIN_DATA/DOMAIN_TRADING`，而 `DOMAIN_CAPS` 的「两集互斥且并集=CAPABILITIES」守门（`markets.py:152-155`）会红；D32:127 只说扩 CAPABILITIES/KIND_CAP_CLASS（P1-6）。

修法：§6.6 表格改为「保留物 + 去向」两列；明确 `research` 所属配置域；明确 hub/worker 双运行时。

---

### Q7 `ProviderPlugin.build(account)->Runtime` 与 Broker / td_registry / ExecutionAdapter 三截的映射？

**可合并「装配 + 执行」，不能合并「凭证读解密/配置校验」「连接窗」「号段分配」，判否。**

| 现行职责 | 能否并入插件 | 证据 |
|---|---|---|
| Broker 凭证解密（Fernet） | **不能无脑并**：是跨 provider 安全横切；并入=每插件复制一份解密 | `broker.py:29-51`（`_BaseBroker.get_credentials`） |
| `get_interface_row` 读行 + required_fields + `md_only` | **不能**：hub 用 `md_only=True` 跳过空凭证（加密），worker 不跳；`build(account)` 无角色参数 | `broker.py:216-263`；`md_hub/main.py:228-232`；`td_registry.py:152` |
| td_registry 连接窗 | **不能**：XTP TD 与 EMT TD 共用同一窗配置（`xtp_session_lead_min/lag_min`），且窗判定需交易日历（在数据层） | `td_registry.py:65-72/144/161-162`；`md_session.py:316-323` |
| 号段 client_id | **不能**：跨进程唯一（hub 默认号、每 worker 派生号，tid 差 98 撞号） | `broker.py:128-137`；D26:68；`td_registry.py:143/164-165` |
| 装配/建连/执行/回报映射 | 可以（按 role 构建） | `td_registry.py:51-173`、`adapters.py:230-970` |

修法：定义 `Account` DTO（row_id/provider/解密后凭证/params/capabilities/market/role）由通用层构造，插件只 `build(account, role)`；号段分配、连接窗判定、凭证轮换检测留在编排/通用层；插件保留连接原语与重连。

---

## 二、P0 清单（8 项，必改）

### P0-1 四维不完备：账户准入、可用资金/保证金、行情数据授权无归属
- D32：`docs/design/D32-接入插件架构.md:64-72`（四维表）、`:116-118`（AccountPort 仅 credentials/health）、`:136`（Broker→ExecutionPort）、`:180-188`（RuleSet 方法）
- 对照：`server/src/risk_control/risk.py:320-352`（market_op + account_allows 两段准入）、`server/src/data_platform/perms.py:84-166`（category/exchange/board/ST/可转债五级权限）、`server/src/risk_control/risk.py:251-397`（无现金/保证金前置）、`docs/design/D26-市场接入架构.md:65`（L2 授权费按账号）
- 修法：四维改五维（加 Account：entitlements/cash_margin/data_license/halt_state），或在 §五表下逐项钉死归属与 fail-closed 消费点。

### P0-2 MarketSpec/InstrumentSpec 新建表制造第二/第三真源
- D32：`:146-156`（market_spec 字段）、`:158-174`（instrument_spec 字段）、`:177-178`（声称复用却新建表）、`:285`（P2 建表）
- 对照：`server/src/quant_common/markets.py:14-32`（MARKETS=MarketSpec 骨架；market_hours=运营真源）、`server/src/data_platform/security_master.py:235-366`（market_hours/anchor/sessions/band_rules 读侧）、`server/migrations/versions/0092_security_master.py:72-101`（band_rules+market_hours 种子）、`0092:105-134` 与 `security_master.py:67-70`（tick_size/multiplier/trade_phase 已在主档）
- 修法：不建 `market_spec`/`instrument_spec`；改为「现有表字段映射 + 仅列缺列迁移」，§7.2 的复用雏形与存储声明保持一致。

### P0-3 RuleSet 跨维合成缺交易所侧输入（有效申报范围无法合成）
- D32：`:155`（MarketSpec 无价格范围）、`:171`（instrument 仅常量 lower/upper）、`:190`（声称品种×交易所）
- 对照：`server/migrations/versions/0092_security_master.py:72-77`（band_rules 仅 pct）、`:81-91`（market_hours 仅 phase/scope）；全仓无申报范围实现
- 修法：MarketSpec 增 `order_price_range`（exchange[:board]×phase，引用价+pct/绝对值/venue 三模式），并写死合成顺序与缺配置 fail-closed。

### P0-4 ProviderRuntime 聚合全部能力，与 D26「hub 零 TD / worker 零 MD」进程拆分冲突
- D32：`:76-82`（resolve 返回 driver）、`:248-256`（ProviderRuntime 五端口、build(account) 无 role）
- 对照：`docs/design/D26-市场接入架构.md:56-62`、`server/src/strategy_framework/md_gateway.py:42-46`（ThinGateway.send_order raise）、`server/src/strategy_runner/td_registry.py:126`（只连 TD）、`server/src/md_hub/main.py:225-232`（只建 MD 网关）
- 修法：`build(account, role: md|td|pull) -> Runtime`，只提供该角色端口；文档明示同账号存在两个运行时（hub MD / 每任务 worker TD）。

### P0-5 号段插件内部实现丢失跨进程唯一性；「同账号一套生效」与多 worker 事实相反
- D32：`:230`（manifest client_id range）、`:264-268`（号段插件内部）、`:295`/`:301`（同账号单套生效）
- 对照：`server/src/strategy_framework/broker.py:128-137`（tid→2..99 派生）、`docs/design/D26-市场接入架构.md:68`（tid 差 98 撞号，建任务时校验）、`server/src/strategy_runner/td_registry.py:143/164-165`（EMT 20..119）
- 修法：号段=编排层全局分配器（account×provider 唯一，DB/租约），插件只声明合法域；约束改为「同账号同角色单实例，hub+N worker 并存」。

### P0-6 端口缺 hub/worker 依赖的生命周期与推送原语；映射漏两个注册表
- D32：`:106-109`（MarketDataPort）、`:96-104`（ExecutionPort）、`:280-293`（迁移映射）
- 对照：`server/src/strategy_framework/md_gateway.py:78-115`（start_ready/connected/poll_supervise/set_on_tick/set_context）、`server/src/md_hub/main.py:334/346/399/418`（消费这些原语）、`server/src/strategy_runner/hub_worker.py:794-815`（connect_status）、`server/src/strategy_runner/main.py:354-366/534`（EVENT_ORDER 注册、窗开 gw.connect）、`server/src/data_platform/interfaces/base.py:74-91`（配置面注册表，映射表未列）
- 修法：端口补 `connected/start_ready/poll_supervise/set_on_tick/connect/close/subscribe_order/subscribe_trade`；§11 增加 `md_gateway.py`、`interfaces/*`、`adapters/base.py _ADAPTERS` 三项归并/退役条目。

### P0-7 停牌/临停无运行时判定输入（静态布尔 + tick 无状态字段）
- D32：`:172`（venue_flags.convertible_halt）、`:184`（validate 列表无停牌）
- 对照：`server/src/strategy_framework/md_gateway.py:8-20`（tick 契约无 halt/status 字段）、`server/src/quant_common/contract.py:35`（suspend 仅 DataKind）、`server/src/risk_control/risk.py:251-397`（无停牌判定）
- 修法：`RuleSet.pre_trade_checks` 增停牌源（suspend 日历 + 当日停牌表 + 行情新鲜度/连续零成交），并要求 tick/事件契约带状态位。

### P0-8 执行端口只有轮询查询，丢订单/成交推送语义
- D32：`:96-104`（ExecutionPort 仅 query_*）
- 对照：`server/src/strategy_runner/main.py:354-366`（EVENT_ORDER→状态流转）、`server/src/strategy_runner/hub_worker.py:444-455`（EVENT_TRADE→trade_log）、`server/src/strategy_runner/trading.py:296-297/780-782`（用 vnpy Status 判断在场单）
- 修法：端口增 `subscribe_order/subscribe_trade`（或 `stream()`）并定义领域状态枚举；写明 EventEngine 退役条件与迁移等价断言。

---

## 三、P1 清单（14 项）

- **P1-1 资金/保证金无硬前置**：D32:71/180-188；对照 `strategy.py:331-344`（只用于选量）、`risk.py:251-397`（无现金门）、`risk.py:426-438`（仅杠杆上限）。修法：定义账户风控阶段（资金/保证金/敞口）在规则校验前，fail-closed。
- **P1-2 InstrumentSpec 不能表达四类衍生品规则；derivative_flags 是代码逃逸口**：D32:160-173/299；对照 `adapters.py:324`（Offset.NONE）。修法：明示首批不含衍生品，删除「插件子规则扩展位」。
- **P1-3 resolve 返回实体绕过端口；组合器层/注册表位置未定义；providers 不在 LAYER**：D32:76-88/62；对照 `server/tests/test_layering.py:12-35`。修法：resolve 归编排、返回端口；providers 入 LAYER 并加守门。
- **P1-4 凭证/连接/健康三方重叠；缺凭证轮换生命周期**：D32:116-118/256/264-268；对照 D26:58-62/120、`md_hub/main.py:80-110`（凭证摘要轮换检测）、`broker.py:216-263`。修法：加单一归属表，生命周期加 reconfigure/凭证指纹。
- **P1-5 §6.6 收敛丢限速/熔断/用量/配额/连接测试；现状是三/四个注册表**：D32:129-142；对照 `data_source.py:26-113`、`interfaces/base.py:74-91`、`adapters/base.py:8-16`、`mgmt.py:317-327`。修法：按「保留物+去向」重写表格，pacing/breaker/usage 明留 A03。
- **P1-6 research token 无域无 DataKind，会撞 DOMAIN_CAPS 互斥并集守门**：D32:127/197；对照 `markets.py:48-77/152-155`、D25:5。修法：定义 DOMAIN_RESEARCH 或扩 DOMAIN_DATA，并同步 test_markets_registry。
- **P1-7 build(account) 的 Account 契约缺失（谁读行/解密/必填/md_only）**：D32:240-250；对照 `broker.py:29-51/216-263`、`md_hub/main.py:228-232`（md_only=True）、`td_registry.py:152`（不传 md_only）。修法：定义 Account DTO + role；解密/校验留通用层，插件不读 DB。
- **P1-8 连接窗「插件内部」与共享窗/日历输入矛盾**：D32:228-229/264-267；对照 `td_registry.py:65-72/144/161-162`（EMT 复用 XTP 窗）、`md_session.py:316-323`、`main.py:534`。修法：编排算 in_session、插件执行 connect/suspend；窗配置单源。
- **P1-9 T+1 三真源（settlement / t_plus / trade_phase）**：D32:153/167；对照 `security_master.py:70`、迁移 `0092:111/121/131`。修法：单源=security_master.trade_phase；MarketSpec 不写 settlement。
- **P1-10 place() 伪代码绕过风控/WAL/审计且未列全部下单入口**：D32:84-88；对照 `strategy.py:459-507`（check_order→WAL→send）、`trading.py:532-640`（急停全平独立路径）、`risk.py:251-397`。修法：明确 place() 只是规则阶段，插入点在 check_order 与 WAL 之后；列出策略/急停/手工/IM 全入口。
- **P1-11 RuleSet 混入 ProviderCaps（能力协商≠交易规则）**：D32:53/80；对照 `markets.py:61-77`。修法：RuleSet 只含 Market×Instrument；caps 留在 resolve 与可选订单类型。
- **P1-12 域模型字段未定义，Position/AccountState 会丢 direction/frozen/yd_volume/available**：D32:125；对照 `adapters.py:37-46`、`strategy.py:355-370`、`trading.py:555-572`。修法：D32 增字段契约表。
- **P1-13 vnpy 泄漏不止 import（编排层用 Status/Direction/EventEngine，S1 扫不到）**：D32:62/308-315；对照 `trading.py:81-83/296-297/514/658`、`main.py:19/354-366`、`hub_worker.py:795`。修法：定义 OrderStatus/方向域枚举；S1 加 AST 类型名扫描；列事件引擎退役条件。
- **P1-14 RuleSet 与 RiskRule 两套规则抽象关系未定（RiskRule 现状未接入 check_order）**：D32:139-142/71；对照 `risk_control/risk_rule.py:20-66`、`risk.py:121-125`（自认两套不同）。修法：明确二者分工（合规规则 vs 风控策略）与唯一执行序列，或给 RiskRule 退役/接入计划。

---

## 四、P2 清单（4 项）

- **P2-1 manifest 示例能力与现状不符**：D32:224-232 给 xtp 声明 `hist_quote/ref_data`；对照 `markets.py:121-123`（xtp 仅 trading/rt_quote）。修法：示例改 `["trading","rt_quote"]`。
- **P2-2 S2「禁 if category ==」会误伤规则层按表键查规则**：D32:309 vs :176 优先级。修法：S2 限定为编排/端口对 provider 字符串的分支，规则层表键查不算。
- **P2-3 开放问题 1（域模型自建 vs 边界转换）应在立项前定**：D32:320；影响 P1 全批次。修法：按决策 1 定稿为自建，删除开放项。
- **P2-4 research token 扩列时点与 P6 实现顺序未定（P1 扩会引入零消费者 token）**：D32:127 vs :217。修法：token 扩列挂 P6 前置，不进 P1。

---

## 五、最小修复顺序（建议）

1. **先改文档再立项**：P0-2（复用现有表）、P0-3（补交易所侧规则字段）、P0-1（补 Account 维/归属表）——这三项决定 P1/P2 批次的表结构，返工成本最高。
2. **P0-4/P0-5/P0-6 一起改**：把「插件实体」降为「按角色工厂」，把「端口」补成 hub/worker 真消费面，把「号段/连接窗」明确留在编排；否则 P3 XTP 切换无法做到 S7 等价。
3. **P0-7/P0-8 在 P1 端口批次前定契约**（停牌输入、订单/成交推送），否则端口一旦冻结，后续加方法要重审。
4. P1 项在对应批次任务文件中逐条落「验收断言」，尤其 P1-10（全下单入口统一管线）、P1-13（AST 守门）必须进守门测试。

**最终结论：须改后再审。**
