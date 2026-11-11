# D32 一致性盲审（C 审：与既有体系）· 只读

> 审对象：`docs/design/D32-接入插件架构.md`（325 行，架构定稿未实施）
> 范围：A01–A05、D02/D06/D20/D22/D25/D26/D28–D31、接口契约、模块契约、CLAUDE.md、flow/待办.md
> 结论：**须改后再审**（5 P0 / 11 P1 / 8 P2）

## 一、必答七问（先给结论）

1. **D32 与 A02「行=账号/列=能力集」是否仍自洽？** **否**。三重失配：①A02 正文仍用 D25 已退役的 `daily/minute/snapshot/quote` 四旧词（A02:12-13/32-34），而 D32 断言「A02 现为 5 token」（D32:127）；②`external_interface` 已于迁移 0116 拆表、0122 契约删除，现行是 `data_source` + `trading_account` 两表，而 A02/A05/D26/D32 仍以单表为前件（A02:11、A05:34、D26:56、D32:5）；③D32 §6.6 把 DataSource 收窄为 MarketDataPort+HistoryPort，丢掉 A02/D25 的 `ref_data`/`inst_event`/基本面拉取。
2. **D32 与 D26 边界是否两真源？** 拓扑/迁移清单边界清楚；但「连接窗/号段/重连」归属冲突：D26 写明「连接生命周期=hub 内部状态机」且 client_id 占用校验在建任务面（D26:60-68），D32 写明「连接窗/号段/重连全在插件、编排层无感知」（D32:256/266-267）。D26 §3.2 重写后无残留插件细节（合格），残留的是 §3.3 生命周期归属。
3. **与 A03/D28–D31 是否重叠？** **是**，且「数据供给归 A03」未落实：D32 自定义 `HistoryPort.fetch(HistoryRequest)->list[Bar]`（D32:120-121）与 A03 `PullCapability.fetch(FetchRequest)->ContractFrame`（A03:276-278）是两套历史拉取契约；D32 §十再建 provider 注册表（D32:272），A03/A04 已有 `@adapter`+CapabilityDecl+registry（A03:274-290、A04:121-135）。窗口/地板/游标（D31:21-30）在 D32 中无归属声明，插件越权实现即破「一个窗口函数」。
4. **执行端口/领域模型与现行契约是否冲突？** **是**。D32 称 `ExecutionAdapter` 是 `ExecutionPort` 的「现行实现」（D32:142），但签名不等价：`send_order->str` vs `send->OrderReceipt`、`cancel_order` vs `cancel`、`query_position` vs `query_positions`、缺 `order_id_map`（D32:96-104 vs 接口契约:294-310）。且 D32 把 `Order/Position` 迁 `quant_common/domain`（D32:125），现行定义在 `adapters.py`（接口契约:267-310），未写迁移期「禁双定义」。
5. **research 第 6 token 三处同改是否写清？** **否，且当前写法不可实施**。D32 §三明确「研究域不是 DataKind」（D32:35/41），§六却要求「同步扩 `CAPABILITIES` 与 `KIND_CAP_CLASS`」（D32:127）；而守门断言 `set(KIND_CAP_CLASS.values()) == set(CAPABILITIES)`（server/tests/test_markets_registry.py:19-20）。无 DataKind 映射 research 时此断言必红。D25 的合法路径是「待消费落地再把 announcement/news 进 DataKind」（D25:107），D32 未引用该路径。
6. **索引/导航一致性？** 基本齐，4 处偏差见 §四。D01–D33 无缺号无重号（D18/D24 的附录式副编号已在索引注明）；D32 索引行与文内一致；但 `docs/design/README.md:34` 的 D26 描述仍是「扁平 provider 插件」（与 D26 §3.2 重写相矛）、`docs/architecture/README.md:10` 的设计层清单漏 D27–D31/D33、`flow/待办.md:20` 的「急停方案③（常驻执行器）」全仓无指针落点。
7. **是否有已被取代但未标注的旧口径？** 有：A01:21「各市场执行规则下沉到 ExecutionAdapter」与 D32 §一.4/§五「规则不属 provider/adapter」直接相反且 A01 无 D32 注记；A01:110/274「XTP MD 单实例」仍是 D26 前口径（无 D26 注记）；接口契约/模块契约无「目标态 D32、现行以代码为准」的注记。

---

## 二、P0（两真源/矛盾，必改）

### P0-1 双 provider 注册表 + 双能力声明
- 证据：D32:272「`REGISTRY: dict[provider_id, ProviderPlugin]`；discover 扫描 providers/」+ D32:55；对照 A03:274-290（`PullCapability/StreamCapability` + data_platform/registry.py）与 A04:121-135（`CapabilityDecl` + `@adapter` 即时校验）。
- 问题：两套注册、两套能力真源；D32 §十一 又说 tushare/jq「归 A03（只共享接入骨架）」，但 manifest 仍声明 `hist_quote/ref_data`（D32:225），实际是第二套数据源注册。
- 修法：在 §十/§十一 钉单一归属——数据拉取 provider 的注册与能力声明唯一归 A03 registry，D32 REGISTRY 仅覆盖执行/实时行情/研究；或明确「A03 registry 即 D32 manifest 的存储实现」，删并列措辞。

### P0-2 声明式规格与 A03 SecurityMaster/节奏域双真源
- 证据：D32:148-178（`MarketSpec.sessions/auction_phases/settlement/currency` + `InstrumentSpec.tick_size/lot_size/t_plus/price_band` + 新表 `market_spec`/`instrument_spec`，D32:178/285）；对照 A03:216-238（`security_master.tick_size/multiplier/trade_phase` + `security_state(kind='limit_band'/'conv_price')`）与 A03:244-262（`market_hours.sessions` 运营真源）、markets.py:19-40（`MARKETS.timezone/sessions_skeleton`）。
- 问题：同一事实（时段、tick、乘数、T+、价带）出现 3 份来源：markets.py 骨架 / A03 三表 / D32 新表；D32 一边说「band_rules + security_master 直接并入、不另起炉灶」（D32:177），一边新建两张表，自相矛盾。
- 修法：把 `market_spec`/`instrument_spec` 定义为 `security_master + security_state + market_hours + band_rules` 的**视图/覆盖层**（或写明一次性迁移合并、旧表退役），并在 §七 逐字段标注真源表；`MarketSpec.sessions` 必须引用 A03 market_hours，不另存。

### P0-3 research token 与能力集守门在数学上互斥
- 证据：D32:35/41（研究域「不是 DataKind」）+ D32:127（「同步扩 `CAPABILITIES` 与 `KIND_CAP_CLASS`」+ 守门一并改）；markets.py:51/61；server/tests/test_markets_registry.py:19-20（`KIND_CAP_CLASS` 值域=CAPABILITIES）。
- 问题：`KIND_CAP_CLASS` 是 DataKind→能力集映射；不新增 DataKind 就无法让 research 成为其值域成员；新增则违背 §三「research 非 DataKind」。两种改法在现文里同时被禁止。
- 修法：二选一并写死——(a) 新增 `research_doc`（或 announcement/news）DataKind 并映射 `research`，同步 D25 §八「立词」路径；或 (b) research 作为独立能力域不进 `KIND_CAP_CLASS`，另立守门；删「同步扩 KIND_CAP_CLASS」的含糊句，并写明 `CAPABILITIES` 与 `KIND_CAP_CLASS` 值域断言的改法。

### P0-4 「行=账号」前件已废（external_interface 已不存在）
- 证据：D32:5 以 A02 为前件；A02:11、A05:34、D26:56/159 均写 `external_interface`；对照 `server/migrations/versions/0116_split_external_interface.py`（拆 `data_source`+`trading_account`）与 `0122_contract_drop_external_interface.py`（删旧表）；模块契约 data_platform.md:364 已按两表书写。
- 问题：D32 的账号/provider 行、迁移清单（P3/P4）若按单表落码必然找不到对象；A05 的 Account FK 定义也指向已删表。
- 修法：D32 §三/§十/§十一 改「数据源行=`data_source`、交易账号行=`trading_account`」，并在 A02/A05/D26 补 0116/0122 后态注记（D32 至少写明前件已按两表落地）。

### P0-5 §6.6 六大接口映射自相矛盾且语义错位
- 证据：D32:135「Broker → ExecutionPort」；对照 D32:14 自述「凭证=`Broker`、执行接口=`ExecutionAdapter`」，接口契约:45-61（Broker 仅 `get_credentials/test_connection`）与接口契约:294-310（执行在 ExecutionAdapter）。D32:136「DataSource → MarketDataPort+HistoryPort」丢掉 ref_data/inst_event（A02:30-35、D25:47-54）。
- 问题：Broker 不是执行面；DataSource 也不是只做行情+历史，它是全 DataKind 拉取入口。按此表迁移会把凭证/配置层误当执行层，并把参考数据/事件拉取挤出端口。
- 修法：改为「Broker（凭证/配置）+ ExecutionAdapter → ExecutionPort（凭证解析收进插件）」与「DataSource → A03 PullCapability（覆盖全部 DataKind）或新增 RefDataPort」；§6.6 表逐行给出「现行类→目标端口→迁移批」。

---

## 三、P1（口径冲突/易误导，实施前应改）

1. **AccountPort.credentials() 越界暴露凭证**：D32:116-118 vs D32:9/58（凭证解析在插件内）+ A03:333-339（解密链）。修法：删 `credentials()`，端口只留 `health()`；凭证由插件按账号行自解。
2. **HistoryPort 与 A03 Pull 双抽象**：D32:120-121 vs A03:276-278/A04:93-108。修法：HistoryPort 定义为 A03 DataBus 的薄门面（入参 `DataRequest`、出参 `ContractFrame`），或从 D32 端口清单删除；并写明 D31 窗口/地板归 A03，插件只做无状态区间抓取。
3. **能力协商漏 DB 启用子集**：D32:273 `pick(account.provider, need)` 只比 manifest；A02:12-13（配置列=用户启用子集，写侧 ⊆）与 A03:356-358（DB enabled+capabilities 硬过滤）。修法：`pick` 签名与算法加入 `enabled ∧ db_caps`。
4. **manifest.priority 违反「偏好住 DB」**：D32:274（代码内 priority 排序）vs A03:363-368（position/quality/health 软排序）+ A03:781（映射住代码、偏好住 DB）。修法：排序仍归 `routing_policy`+账号 position；manifest.priority 删除或降为同分 tie-break。
5. **连接窗/号段归属 D26↔D32 冲突**：D26:60-68（连接生命周期=hub 内部状态机、client_id 建任务校验）vs D32:256/266-267（全在插件）。修法：D26 §3.3 补指针「连接状态机所在运行时由插件提供，hub 仅承载进程」；client_id 占用校验改插件内或明确为插件 API。
6. **provider 两把键被合并**：D32:272/§四（`provider_id` 单键、`providers/binance|okx/`）vs markets.py:129-139（`binance`=数据源、`binance_perp`=交易通道，注释明示混用会静默拉错源）。修法：REGISTRY 键=（表, provider）复合或 manifest 增 `roles:[data,trading]`，保留两键。
7. **「ExecutionAdapter 是 ExecutionPort 现行实现」不成立**：D32:142 vs D32:96-104/接口契约:294-310（方法名/返回型四项不等）。修法：改为「语义对应、签名不等价，P3 增设端口适配层」，并列签名映射表。
8. **A02 仍是四旧 token，D32 的事实陈述错误**：A02:12-13/32-34/46 vs D25:44-59/markets.py:51。修法：重写 A02 §一/§三/§五为 5 token（或加「D25 后旧词仅历史」注记），再谈 D32 第 6 token 扩展。
9. **A01 规则落点与 D32 相反且无注记**：A01:21「执行规则下沉到 ExecutionAdapter」vs D32:17/91（规则属 `RuleSet`，不属 provider）。修法：A01 §2.3 改写为「差异=provider 插件+声明式规格」，并加 D32 关系注记。
10. **InstrumentSpec 品类枚举与代码不一致**：D32:162（stock/listed_fund/convertible/perp/future/option）vs markets.py:48（含 `fund`/`reits`，无 future/option）；A05:58 仍写 `etf`（已正名 listed_fund）。修法：枚举以 `ASSET_CATEGORIES` 为基集并显式声明扩展项；A05 顺手正名。
11. **契约文档无「目标态」标注**：接口契约:294-310 与 模块契约/adapters.md:82-86/118 仍把 adapter 与 vnpy lazy import 当稳定契约，D32 S1 要求 providers 外零 `import vnpy`（D32:260/308）。修法：两处加一句「D32 目标态：adapter 迁移至 providers/，届时本节改写；P1–P5 期间以现行代码为准」，免实施者按 S1 直接判现行代码违规。

---

## 四、P2（一致性瑕疵/导航）

1. `docs/design/README.md:34` D26 描述仍为「MarketSpec 声明+扁平 provider 插件」，与 D26:46-52 重写矛盾 → 改为「账号级 hub 拓扑 + 生命周期（插件细节见 D32）」。
2. `docs/architecture/README.md:10` 设计层清单只列 D01-D19/D20-D23/D24/D25/D26/D32，漏 D27–D31、D33 → 补齐区间；:24 hub 行未提 D32（仅 :29 提）。
3. `A01:110/274`「XTP MD 单实例」是 D26 前形态且无注记 → 加「D26 终态=账号级 hub」指针。
4. `D32:156-157` 市场枚举含 `hk/us/futures`，`MARKETS` 仅 astock/crypto（markets.py:19-27）→ 写明扩展市场须同步注册表，否则组合器 `MarketSpec.of` 无源。
5. `D32:311` 要求五品种矩阵含「期货」，但 §二 非目标明确首批不做期货（D32:24）→ 删期货出 S5 或标「仅架构占位断言」。
6. `flow/待办.md:20`「急停方案③（常驻执行器）」全仓无落点（仅此一处）→ 补指针或删括注。
7. `D32:225` manifest.capabilities 与 D32:97 `ExecutionPort.capabilities()` 两个能力来源 → 声明 manifest 为唯一声明，端口方法返回其投影。
8. `D32:285-292` 迁移表漏现有 `create_md_gateway`（md_hub.md:23/72）、`_TD_BUILDERS/build_td_runtime`（strategy_runner.md:20）、`get_data_source/get_broker` 工厂 → 逐项补行，否则 P3 会漏拆。

## 五、索引点验（item 6 明细）

| 检查 | 结果 |
|---|---|
| D01–D33 缺号/重号 | 无缺号；D18、D24 的副文件已在索引注明为附录，可接受 |
| `docs/design/README.md` D32 行与文内一致 | 一致（「架构定稿，实施待立项」） |
| 同表 D26 行 | **不一致**（扁平 provider 旧词，见 P2-1） |
| `docs/architecture/README.md` | 设计层清单不完整（P2-2）；场景表 :29 已含 D32 |
| `CLAUDE.md:107` | 一致（D26+D32 合并索引，P1–P5/P6 分工与 D32 一致） |
| `flow/待办.md:20-21` | D32 文件指针有效；「急停方案③」指针失效（P2-6） |

## 六、最小修订清单（按序）

1. 先定 **P0-1/P0-2/P0-3** 三处单一真源（注册表、规格表、research 能力域）——这三项决定 D32 是否还是「另一套架构」。
2. 改 **P0-4/P0-5** 前件与映射表（两表 + 端口归属）。
3. 再清 P1（尤其 6.6、HistoryPort、pick、priority、连接窗归属）。
4. 最后补索引/注记（P2）。

> 判定：**须改后再审**。P0 未清前立项，P1 批（新建 quant_common/domain、REGISTRY、market_spec）会立刻与 A03/A04 注册表、SecurityMaster、能力集守门三处对撞。
