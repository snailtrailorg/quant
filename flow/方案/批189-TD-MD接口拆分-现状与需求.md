# 批189 · TD/MD 接口拆分 —— 现状调研与需求冻结稿

> 性质：**调研 + 需求**（未定方案）。方案待本文件「待拍板问题」逐条裁定后再写。
> 目标：先把「要什么」与「现状是什么」钉死，避免方案阶段反复。
> 证据口径：仓库代码 + prod 实测（2026-11-18）。凡「待核」项已标注。

## 1. 用户需求（原话归纳）

1. **两个现网毛病必须修**：
   - OKX 行情 hub 每 60s 报 `Failed to subscribe data, symbol not found: 600000_SWAP_OKX`（把 A 股标的当 OKX 永续订阅）；
   - 币安行情 hub 的 websocket 反复 `Connection reset by peer - reconnect`，且 `subs=1 / bars=0`（不产出数据）。
2. **进程随接口生命周期**：TD/MD 接口注册上来就创建对应进程（hub / exec）；进程状态与接口 `enabled` 绑定——启用=运行、停用=停止。
3. **TD 与 MD 彻底解耦**：
   - 即使同一券商/同一交易所，行情接口与交易接口也**分成两条独立配置**；
   - 集成中心把「交易账号」页签拆成「**行情接口**」「**交易接口**」两个页签；
   - 能力集与配置页面按接口类型分别定义；
   - TD 关联 MD 的映射设置同步调整（清仓取价走哪条行情接口）。

## 2. 现状调研

### 2.1 数据模型（一条行 = 一个连接身份）

- 表 `trading_account`（0116 expand 建表；0122 contract 删旧 `external_interface`）。列：
  `id, name, provider, market, credentials_encrypted, params(jsonb), capabilities[], position, enabled, account_key, impl, quote_account_id, created_at, updated_at`。
- **一条行同时承载 MD 与 TD 能力**：XTp 行 `capabilities=['rt_quote','trading']`；EMT 行同构；币安/欧易行也是 `['rt_quote','trading']`（但 `has_credentials=false`）。
- 域能力集（`quant_common/markets.py:153 DOMAIN_CAPS`）：
  - `data_source`（数据源表）：`hist_quote, ref_data, inst_event`
  - `trading_account`（交易账号表）：`rt_quote, trading`
  - 写侧校验 `data_platform/capabilities.check_capability_domain`：能力 ⊆ 本域能力集（越域 400）。
- 行情与交易共用同一 `id`：hub 实例名 `quant-md-hub@<id>`、exec 实例名 `quant-td-exec@<id>`。
- `quote_account_id`（0191，回填 = id）：TD 取价映射（清仓读 `hub:latest_tick:{quote}:{vt}`）。**目前只有 DB 列 + 后端消费，前端无设置入口**。
- 7 张业务表 `account_id` FK 指向 `trading_account.id`：`live_task, position_snapshot, position_refresh, account_snapshot, order_log, trade_log, account_permission`。

### 2.2 Provider / 能力真源

- 插件注册表 `src/registry`：**当前只注册了 xtp**（`src/providers/xtp/manifest.py`：`port_caps={execution, market_data, account}`，`data_kinds=(hist_quote, rt_quote)`，`terminal_domains={td:(2,99), md:(1,1)}`）。
- emt_emq / binance_perp / okx_perp 仍走 **legacy 通路**（`NON_DATA_PROVIDERS` / `list_md_gateway_providers()` / `_ADAPTERS`），不在 D32 插件/端口体系内。
- `exec_capable_providers()` = `pick(domain=trading_account, caps={execution}, mode=plugin)` → **只有 xtp**。因此：
  - `enabled_exec_accounts()` = enabled ∩ execution = **仅账号 1**；
  - EMT 账号 3 有 `trading` 能力但**无 execution**（D32 P4 未做），web 下发与 SA4 都不会给它建 exec。
- MD 网关 provider 集 = legacy `list_md_gateway_providers()` ∪ 注册表 `market_data` → xtp/emt_emq/binance_perp/okx_perp 四家。
- **能力声明与执行载体的口径差**（拆接口时必须正视）：`NON_DATA_PROVIDERS` 里 emt_emq/binance_perp/okx_perp 都声明了 `{trading, rt_quote}`，但 `exec_capable_providers()` 只认 **plugin**（当前仅 xtp）——所以这些 provider「有 trading 能力、无 exec 载体」。拆成交易接口后会出现「交易接口存在但永远不拉进程」的形态，必须在需求里定死：要么插件化（D32 P4），要么交易接口不允许保存/置灰。

### 2.3 进程与生命周期（现状）

| 进程 | 模板 | 期望源（SA4 `scheduler/tasks._desired_units`） | 停用语义 |
|---|---|---|---|
| `quant-md-hub@<id>` | `quant-md-hub@.service`（ACCOUNT_ID=%i） | enabled ∩ `md_providers` | L3 停「非期望数字实例」 |
| `quant-td-exec@<id>` | `quant-td-exec@.service`（ACCOUNT_ID=%i） | `enabled_exec_accounts()`（enabled ∩ execution） | 批188 补：L3 停非期望数字实例 |

- 账号 `enabled` 是唯一开关（SA4 每 300s 对账）。**没有独立的「行情启用/交易启用」两个开关**。
- 「注册即建进程」现状：注册=插入行；systemd 模板常驻；实例只在 SA4 判定期望时 start——禁用时并没有「stopped 的实例」，只是不启动（已有实例会被 stop）。
- 健康监控：hub 用 Valkey 心跳 `quant:hb:md-hub:<id>` 动态发现（不入 CORE_UNITS）；td_exec 用 TTL 在场键 `td:exec:<id>` + `td_exec_down` 沿检测；web 端刚补了 td-exec 卡片。hub 与 exec 都已有进程卡片，但**卡片维度是账号 id**，不是「接口」——拆分后需按接口维度重做。

### 2.4 集成中心与 API

- `web/src/views/Integrations.vue`：TabsShell 六页签——mail / sms / llm / **dataSources（数据源）** / **tradingAccounts（交易账号）** / proxy。
- 通用表格+表单 `web/src/components/InterfacesCard.vue(kind=...)`：
  - 新建时按 provider 页签选型（目录来自 `/api/trading-accounts/providers`）；
  - 字段：name / market（按 provider 推导，只读）/ **capabilities（复选框，值域=DOMAIN_CAPS）** / exchanges+account_key（crypto）/ params（服务端 FIELD_SCHEMA 动态生成）/ 凭证 / enabled；
  - 无 `quote_account_id` 字段（TD→MD 映射前端缺失）。
- `TradingAccounts.vue` = `InterfacesCard kind="trading_account"` + `AccountsCard`（API 密钥账户卡，实盘任务绑定用）。
- 后端族 `web_api/routes/mgmt.py`：`/api/trading-accounts`（GET/POST/更新/删除/排序/连接测试/providers），`cap` 过滤只允许 rt_quote|trading；写侧 `_validate_iface` 校验 provider 归属域 + 能力 ⊆ 代码能力 ⊆ 域能力。
- 数据源族 `/api/data-sources` 同构（kind=data_source）。

### 2.5 TD→MD 取价映射

- `trading_account.quote_account_id`（迁移 0191，默认=id）。
- 消费方：`td_exec/main.py`（启动读一次 → 心跳 base 字段 / 清仓读 `hub:latest_tick:{quote}:{vt}`）、`health_monitor/collector.py`（展示字段）。
- 默认同 id 的隐含前提：**MD 与 TD 是同一行**。拆分后该前提失效，映射必须变成显式引用（且要有缺失校验/告警）。

### 2.6 两个 bug 的定位（已核）

- 共同根因候选：`md_hub/main.py::_desired_symbols()` 的订阅真相源**四源里三源是全局的、未按 provider/账号过滤**：
  1. `SELECT DISTINCT symbol FROM live_task WHERE status='running'`（**全账号** running 任务标的）
  2. `system_config.hub_shadow_symbols`（全局白名单）
  3. `hub_transient_subs`（详情页「看过即订阅」，**全局**表，无 provider/account 维度）
  4. 只有持仓源按 `position_snapshot.account_id=<本 hub>` 过滤。
  ⇒ 每个 hub 都会拿到 A 股标的（600000 等），交给各自网关翻译：
  - OKX 网关把裸码翻译成 `600000_SWAP_OKX` → 订阅失败（每 60s 一条）；
  - 币安网关对非法 symbol 的处理是服务端断链 → `Connection reset by peer` → 重连循环（与 `subs=1 / bars=0` 现象一致）。
- 待核：币安是否还有代理/网络层原因（需看网关 ws 连接参数与异常码；本次只从现象与共源推断）。
- 另：`hub_shadow_symbols` 与 `hub_transient_subs` 的设计本身缺 `provider/market` 维度——拆分后正好可以按「行情接口」维度重做。

### 2.7 守门与文档现状

- 能力：能力域/子集校验的守门散在 `tests/test_batch99_sync_backfill_capability.py` 与 mgmt 路由测试中（无单一 `test_interface_capabilities`）；另有 `test_no_sql_in_new_routes`、`test_route_auth_gate`、`test_layering`。
- 进程：`tests/test_sa4_reconciler.py`、`tests/test_batch188_td_exec.py`（含 SA4 第四源/非期望停）。
- 文档：`docs/architecture/A02-外部接口与市场维度设计.md`（行边界=账号/列=能力/页签=过滤视图）、`docs/design/D25-三层能力模型.md`、`D26-市场接入架构.md`、`D32-接入插件架构.md`、`A06` 波次、`CLAUDE.md` 核心约束。

## 3. 目标态需求（使用者视角，可验收）

> 用「我做为使用者要能看到/做到什么」表述，作为验收口径。

### R1 接口实体拆分
- 行情接口（MD）与交易接口（TD）是**两条独立记录**：各自 provider、凭证、参数、能力集、启用开关、备注。
- 同一券商（如中泰 XTP）会出现两行：`中泰XTP-行情`、`中泰XTP-交易`；同一交易所（币安）同理。
- 能力集按接口类型收敛：
  - 行情接口：`rt_quote`（并预留 `hist_quote/ref_data/inst_event` 若未来走同一实体）；
  - 交易接口：`trading`（execution 能力由 provider 决定，不靠勾选绕过）。
- **验收**：新建一个交易接口时，能力选项里不出现行情能力；反之亦然。

### R2 集成中心页签拆分
- 「交易账号」页签拆为「**行情接口**」「**交易接口**」两个页签（数据源、LLM、邮件等页签不动）。
- 两个页签各自列表/新建/编辑/删除/排序/连接测试，列集按类型差异化（MD 显示订阅/心跳/最新行情；TD 显示执行进程/关联行情接口）。
- 权限：沿用 `system_config` 还是拆两个键，待拍板（Q4）。
- **验收**：两个页签互不串行；在行情页签建的行不会出现在交易页签。

### R3 进程与接口一一对应
- 每个**行情接口** → 一个 `quant-md-hub@<md_id>`；每个**交易接口** → 一个 `quant-td-exec@<td_id>`。
- 接口 `enabled` 变化后 ≤ 一个对账周期（现 300s，可缩短）内进程状态跟随：启用→active，停用→stopped。
- 注册（新建行）后：进程实例「存在且按 enabled 决定起停」——给使用者看到「已创建/运行中/已停止」三态。
- 删除接口：先停进程，再删行（行删除不得留下孤儿进程）。
- **验收**：在页面上停用行情接口 → `systemctl is-active quant-md-hub@<id>` 变 inactive；再启用 → 变 active；交易接口同理。

### R4 TD→MD 映射
- 交易接口必须指定一个「取价行情接口」（同一市场、具备 `rt_quote`）；未指定时的行为必须显式（默认同市场唯一 MD？还是拒绝保存？待拍板 Q5）。
- 清仓取价、hub 订阅（持仓标的进入该 MD 的订阅集）、风控行情快照都按映射解析。
- 映射变更后：exec 需要重读（重启或热更新），并在页面上可见。
- **验收**：把 TD 的映射从 MD-A 改到 MD-B，清仓日志显示读的是 `hub:latest_tick:<MD-B>:<vt>`。

### R5 存量迁移
- 现有 4 行（XTP/EMT/币安/欧易）如何拆：
  - XTP：拆成「XTP-行情」+「XTP-交易」，历史 `account_id=1` 的数据归属谁？——**交易接口沿用旧 id**（否则 order/trade/position 全部要重映射），行情接口新建 id；`quote_account_id` 指向新 MD id。
  - EMT：同构（但其 TD 尚未插件化，是否本期就建 TD 行待 Q6）。
  - 币安/欧易：无凭证，是否本期就拆/或先禁用待 Q7。
- 迁移必须满足破坏性迁移两步走（expand→contract），且上一版代码可回滚。
- **验收**：迁移后 `live_task/order_log/trade_log/position_snapshot` 的 account 语义不变；回滚可跑旧代码。

### R6 两个 bug 的验收
- OKX hub 日志 1 小时内无 `symbol not found: *_SWAP_OKX`；
- 币安 hub websocket 连续 10 分钟无 `Connection reset`，且 `bars` 递增（或该接口明确停用，不再拉起）；
- 订阅源按「行情接口」隔离：A 股标的不会进入加密 hub 的订阅集。

### R7 可观测
- 监控页：每个行情接口卡片（连接状态/订阅数/最新 bar 时间/重连次数），每个交易接口卡片（进程/会话/映射 MD/持仓快照年龄）。
- 告警：接口启用但进程不在场、MD 订阅持续失败、映射缺失/失效。

## 4. 影响范围（六面）

| 面 | 影响点 |
|---|---|
| 数据 | 新实体/角色列（表结构二选一，见 Q1）；迁移 0191 语义改写；`quote_account_id` FK/约束；7 张 FK 表的 account 语义；subscribe 源表补 provider/market 维度（hub_transient_subs/hub_shadow_symbols）；`provider_session_slot(account_id, provider, role)` 维度不变但 id 语义变 |
| 写入 | mgmt 端点族（新增 MD/TD 两族或 kind 参数化）；写侧能力校验（域能力集拆两套）；SA4 `_desired_units` 改两源；启用联动（写 enabled 时即时 reconcile？）；删除联动停进程 |
| 读/展示 | Integration 两页签；InterfacesCard 按类型裁剪字段；映射选择器；监控卡片（hub 卡 + exec 卡 + 接口维度）；i18n 三语；权限键（Q4） |
| 可观测 | `health_monitor` 期望集两套；hub 心跳卡片化；`td_exec_down` 改按交易接口；订阅失败告警按接口聚合 |
| 测试 | 域能力/路由鉴权/分层；SA4 期望/停止；td_exec 映射；迁移往返；两个 bug 的回归（订阅隔离） |
| 文档 | A02 行边界重写（账号→接口）、D25 能力模型、D26 拓扑、D32 manifest、A06 波次（unit 名不变但 id 语义变）、CLAUDE.md 核心约束、操作手册、D12 取价段 |

### 4.1 代码级改动点（初清点，方案阶段再收敛）

- `server/src/quant_common/markets.py`：DOMAIN_CAPS / DOMAIN_* 词表（新增接口类型或改域）。
- `server/src/data_platform/capabilities.py`、`specs.py`、`config_store.py`：域能力、账号/接口读写、`enabled_exec_accounts`/`enabled_md_interfaces`。
- `server/src/registry/__init__.py` + `providers/*/manifest.py`：端口能力声明与 provider 归属（拆 MD/TD 两个角色声明）。
- `server/src/scheduler/tasks.py`：`_desired_units` 两条源改接口表；L3 停非期望实例；启用联动周期。
- `server/src/md_hub/main.py`：`_desired_symbols` 按接口/市场过滤（bug 根因）；`_poll_iface_switch` 行来源改 MD 接口。
- `server/src/td_exec/main.py`：映射读取（quote 接口 id）、启动校验、心跳 base 字段。
- `server/src/web_api/routes/mgmt.py`（或新 routes/interfaces.py）：两族端点/校验/审计。
- `server/src/web_api/routes/risk.py`、`strategy.py`、`tasks.py`：下发账号集（exec 接口）不变但 id 来源变。
- `server/src/health_monitor/*`：两套期望集 + 卡片数据。
- `web/src/views/Integrations.vue` + `components/InterfacesCard.vue` + 新页签组件 + `ConnectionCards.vue` + locales。
- `server/scripts/systemd/*.service`：模板不变（ACCOUNT_ID → INTERFACE_ID 改名可选）。
- 迁移：新表/新列 + 存量拆分 + `quote_account_id` 重映射 + 订阅表维度补齐。

## 5. 待拍板问题（Q1–Q10）

- ~~Q1 实体形态~~ → **D1：行情/交易各一单表（A 方案，待确认「数据源是单表」推理）**。
- ~~Q2 TD:MD~~ → **D2：1:N 主备**。
- **Q3 跨市场映射**：TD(astock) 能否映射 MD(crypto)？（应为否，需要校验）
- **Q4 权限**：两个页签共用 `system_config`，还是新增 `md_config`/`td_config`？（RBAC 注册表要同步）
- ~~Q5 映射缺失~~ → **D3：允许留空，运行时报错**。
- **Q6 EMT 交易**：本期是否把 EMT 纳入 execution（D32 P4）并建 TD 接口？不做则 EMT 只有行情接口。
- **Q7 无凭证账号（币安/欧易）**：本期禁用 / 保留空转 / 拆分后允许「仅行情且匿名」？
- **Q8 「注册即建进程」语义**：停用时要不要在监控/页面上显示「已创建·stopped」（systemd 无实例概念，需 SA4 保持一个 stopped 实例 or 仅 UI 展示期望态）？
- ~~Q9 数据源是否并入行情接口~~ → **D4：不并入，保留独立数据源页签**。
- **Q10 迁移 id 策略**：TD 沿用旧 account id、MD 新建 id？（推荐，保 7 张 FK 数据语义）——需确认历史 `quote_account_id` 与 `position_snapshot.account_id` 的归属。

## 5-A. 已裁定（2026-11-18 用户）

- **D1 实体形态（待确认措辞）**：现状 `data_source` 是**单表**（83a 起每域一张表：`data_source` / `trading_account`）。按「单表则都建成单表」的口径 ⇒ **行情接口、交易接口各建一张单表**（数据源保持 `data_source` 不动），即原 Q1 的 **A 方案**：`market_interface` + `trade_interface` 两表。
  - 原 `trading_account` 行迁为 `trade_interface`（**沿用旧 id**，7 张 FK 与历史数据语义不变）；行情侧新建 `market_interface` 行（新 id）。
  - 数据源页签（tushare/akshare）**不并入**行情接口（见 D4）。
- **D2 TD→MD 关系**：**1:N 主备**——交易接口可关联多个行情接口，带 priority；清仓取价按主→备降级（需定义降级判据：主接口无 tick/断流/未启用）。
- **D3 映射缺失**：**允许留空**；运行时报错（exec 启动不拒、清仓/下单取价时返回明确 `no_quote` 类错误并告警），页面上对该交易接口标黄。
- **D4 数据源归属**：tushare/akshare 等**不算行情接口**，保留独立「数据源」页签；行情接口只承载实时行情（rt_quote 及未来同一链路的 hist/ref_data/inst_event 若走实时通道）。

## 6. 建议切分（供方案阶段参考，非承诺）

1. 批 189a：**两个 bug 快修**（订阅源按 provider/market/接口过滤 + 无凭证接口治理），不依赖拆分。
2. 批 189b：接口实体拆分（expand：新表/新列 + 双写 + 读侧切换 + UI 两页签 + 映射）。
3. 批 189c：contract（删旧列/旧语义）+ 监控卡片 + 文档收口。
