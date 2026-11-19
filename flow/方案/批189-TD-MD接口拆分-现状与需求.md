# 批189 · TD/MD 接口拆分 —— 现状调研与需求（冻结稿 v5）

> v5：按 v4 双盲复审（代理A/B）P0 重写，消除身份/迁移/租约三处矛盾。报告：`flow/审查/批189-需求v4复审-代理A.md` / `-代理B.md`。
> 性质：需求冻结稿，不含实现方案。下一步 M0 契约 + 测试骨架。

## 1. 需求

1. 修两个现网毛病：OKX hub 订阅 A 股标的（`600000_SWAP_OKX`）、币安 hub websocket 反复 reset。
2. TD/MD 彻底解耦：同券商/交易所也拆「行情接口」「交易接口」两条配置；集成中心两页签；能力集与配置页按类型定义；TD→MD 映射同步调整。
3. 接口注册后进程常驻；`enabled` 只决定干活/空转，不决定 systemd 起停。
4. 数据源/行情/交易三类全部插件化；无插件载体的接口不可启用、不建进程。
5. 小步交付：契约先行、模块化、每步完整、边界清晰。
6. 身份规划：code 作为唯一身份（接口主键、业务表外键、unit、Redis 键、API、日志、UI）。

## 2. 现状（代码核对）

### 2.1 数据模型

- `trading_account` 一行同时承载 MD+TD：`id bigserial, name, provider, market, credentials_encrypted, params, capabilities[], position, enabled, account_key, impl, quote_account_id, is_virtual, exchanges, ...`。
- 存量行：`1 XTP / 3 EMT / 4 Binance / 5 OKX`，另有 `is_virtual=true` 虚拟账户（0127 纸交易）。
- 域能力：`data_source={hist_quote,ref_data,inst_event}`、`trading_account={rt_quote,trading}`（`quant_common/markets.py`；测试要求两集互斥且并集=全集）。
- 身份引用共 **8 处**：7 张业务表（`live_task / position_snapshot / position_refresh / account_snapshot / order_log / trade_log / account_permission`）+ `data_source_usage.interface_id`（0090，bigint，无 FK）。
- `provider_session_slot` PK=`(account_id,provider,terminal_id)`，无 role；`position_snapshot/position_refresh` 主键含 `account_id`；`live_task` 有按 id 判 `is_virtual` 的触发器（0174）；`order_log/trade_log` 的 account FK 是 SET NULL。
- `quote_account_id`（0191）仅 td_exec/collector 消费，前端无入口。

### 2.2 身份硬编码（全部要改）

- `md_hub/main.py:168-175`（ACCOUNT_ID 非数字 exit 78）、`td_exec/main.py:382`、`scheduler/tasks.py:2344/2355`（只停数字实例）、`web_api/routes/system.py`（readyz 数字过滤）、`deploy/wrappers/quant-hbcheck`、`health_monitor/collector.py:249`。
- `hub:bars:{account_id}:{symbol}`（databus.py:225、hub_worker.py:98）+ 消息 `account_id` + worker hub 守卫（hub_worker.py:108-116,378-382）。
- 订阅源四源（md_hub `_desired_symbols`）：running live_task 全账号、`hub_shadow_symbols` 全局、`hub_transient_subs` 全局、持仓仅按本行 id → 两 bug 根因。
- `data_license` 按本行 `trading_account.params` 读（subscription.py）；MD 换 id 后静默失效。
- 部署：`quant-dbro`/`quant-hbcheck` 两态选表；hub/td 波次按 DB id 拼 unit；pinned 枚举全部 quant-*。

### 2.3 进程与监控

- `quant-md-hub@<id>` / `quant-td-exec@<id>`（ACCOUNT_ID=%i）；SA4 期望按 enabled 过滤；L3 停非期望数字实例。
- hub 心跳 `quant:hb:md-hub:<id>`；td_exec TTL `td:exec:<id>`；监控卡片按 id。

### 2.4 两个 bug

- 同源：订阅源未按接口/provider/market 隔离。OKX 把裸码译成 `<sym>_SWAP_OKX`；币安对非法 symbol 断链→重连循环。固定在 M4，不做临时补丁。

### 2.5 与既有方案的关系（全覆盖）

| 旧文档 | 冲突 | 处置 | 覆盖 |
|---|---|---|---|
| A02 | 行=账号/列=能力/页签=能力过滤 | 修订：行=接口、页签=类型；保留市场维度/凭证/配置立法 | C1–C6 |
| D25 | 能力 token/域划分（以代码 CAPABILITIES/DOMAIN_CAPS 为准，不在本文写死数字） | 修订：三类接口各一域，互斥+并集守门保留 | R1/C6 |
| D26 | 核心句「一账号=一行=一 hub+一套 TD，MD/TD 同凭证」 | **修订为 v6 接口级拓扑**（不退役，保引用链）；历史理由与 M5 退役保留 | 全文 |
| D32 | manifest 单 domain | 修订：provider 可声明 md/td 双角色 | C6/D5 |
| A06 | 波次按 DB id、wrapper 两态 | 修订：波次取在跑实例（code）、wrapper 按 marker 二选一 | C7/R8 |
| CLAUDE.md | 行边界=账号、能力 5 token | **已修订**：三类接口 + code 身份 + 常驻空转 | 已同步 |
| D12 §2.11 | quote 默认同账号 | 修订：1:N 主备 + 空映射报错 | R4/M5 |

## 3. 目标需求

### R1 三类接口实体
- 行情/交易/数据源各一表，主键 `code TEXT`；同券商拆两行。
- 能力由「接口类型+manifest」派生，页面只读；写侧校验「类型 ⊆ provider 声明」。
- 验收：交易接口不出现行情能力；越域保存 400。

### R2 集成中心两页签
- 「交易账号」→「行情接口」「交易接口」；数据源页签保留；**API 密钥账户卡（AccountsCard）落到交易接口页**（现有功能不得消失）。
- 复用 `InterfacesCard(kind)` + `ColumnSettings`；验收：两页签互不串行、AccountsCard 入口计数一致。

### R3 进程与状态（四值）
- 注册（有载体）→ 进程常驻 active；`enabled` 决定干活/空转；无载体不建进程。
- 状态（展示四值）：**待接入（无载体）/ 运行中·空闲 / 运行中·工作中 / 已停止（异常）**；「已退役」为软删后的内部终态，不在列表展示。
- 注册（有载体）后进程即常驻；`enabled` 只决定干活/空转，不决定 systemd 起停。
- 停用验收：心跳 `working=false`、`ss` 无券商连接、`systemctl is-active` 仍 active。

### R4 TD→MD 映射
- `td_md_map(td_code, md_code, priority)`；priority 唯一决定主备。
- 允许留空：留空→`MAPPING_MISSING`；有映射但无 tick→`NO_QUOTE`（**两码分开**，否则前端标黄与告警口径错）。订阅侧同样不得静默零订阅。
- 主失效按 priority 降级；映射变更 ≤5s 生效；验收：清仓日志显示新 md code。

### R5 迁移（唯一路线：离线窗口）
- 维护窗停服务→**全库备份**→迁移→起服务→冒烟。**不做在线双写**；不保留旧代码回滚（见 R8）。
- 存量：接口行按 provider/market/能力映射成 `md-*/td-*/ds-*`；**例外**：EMT 只建 `md-emt`（交易待接入不建行）；币安/欧易建 `md-*` + `td-*`（td 行 `enabled=false`，无凭证）；8 处身份引用全部回填/改列；业务表 PK/UK/触发器/FK 按新语义重建。
- 兜底（用户授权）：失败或历史无价值 → 基线重置（清业务表+重建+seed 接口），同样先备份。
- 迁移须给窗口预算、锁模式、`statement_timeout`、每步可重入点与校验点（行数/FK 计数）。

### R6 两个 bug 验收
- OKX 1h 无 `*_SWAP_OKX`；币安 10min 无 reset 且 bars 递增（或该接口停用不建会话）。

### R7 观测
- 接口维度卡片；告警 `iface_down/subscription_fail/mapping_missing`。**判活按「有载体」全部接口**（与常驻模型一致）；`working` 只作为工作态展示，不参与存活判定。

### R8 部署与回滚
- 本批 schema 破坏性变更：发布 `-e allow_contract=true -e contract_reason="批189 接口三表拆表"`；**`auto_rollback_disabled` 由 playbook 在「豁免生效」时自动置位，操作者不传**；迁移由 release 阶段 5 自动执行，**不手工跑 alembic**（手工会绕过 DDL 门与留痕）；自动 rollback 不支持。
- 回滚 = 恢复迁移前快照 + 旧版代码；发布前做一次「备份→恢复」演练并在窗口内验证。
- 波次取「在跑实例」；wrapper 按 marker 二选一（新/旧，未知 rc=1）且先装位后发布；pinned 断言覆盖 code 实例。

## 4. 影响面

### 4.1 消费者台账（file:line 内联）

| 面 | 消费者 |
|---|---|
| 身份 | md_hub/main.py:168；td_exec/main.py:382；scheduler/tasks.py:2344,2355；web_api/routes/system.py readyz；health_monitor/collector.py:249；deploy/wrappers/quant-hbcheck |
| 业务表 | live_task(0174 触发器)；position_snapshot/position_refresh(主键)；account_snapshot；order_log/trade_log(SET NULL)；account_permission；data_source_usage.interface_id(0090) |
| bar 流 | databus.py:225；hub_worker.py:98,108-116,378-382；回放 |
| 订阅 | md_hub `_desired_symbols` 四源；data_platform/subscription.py；hub_transient_subs/hub_shadow_symbols |
| 取价 | td_exec quote 解析；hub:latest_tick 读写方（15 文件，M4 清单化） |
| Web/IM | routes/{trading,risk,strategy,mgmt,chat}.py 的 account_id 入参/出参；IM 工具参数 |
| 旁路脚本 | scripts/verify_p3_plugin_lifecycle.py、scripts/test_emt_cancel.py、server/scripts/verify_integration.py、data_platform/stock_detail.py |
| 部署 | release.yml/rollback-tasks.yml 波次；quant-dbro/quant-hbcheck；A06 |

### 4.2 身份与租约（v5 定稿）

- **code 唯一身份**：三表主键、8 处引用、unit、Redis 键、API、日志、UI；`varchar(32) COLLATE "C"`，正则 `^[a-z][a-z0-9-]{1,30}$`（≤31 字符），表级 `CHECK (code LIKE 'md-%')` 等。
- **不用 registry**：三类前缀互斥，跨表唯一由表内 UNIQUE + 前缀约束自然成立；减少中心表与写序耦合。
- **唯一索引按表分别建**：`(market, account_key) WHERE account_key IS NOT NULL AND enabled` 每表一份（不再有同表 md/td 撞索引问题；M1 预检各表重复计数）。
- **租约域改为物理登录**：`provider_session_slot(login_key, provider, role, terminal_id, ...)`，PK=`(login_key, provider, role, terminal_id)`；`login_key`=凭证/账号指纹派生（同券商同账号的 md/td 两行共享 same login_key，MD 用 terminal 1、TD 用 2..99，互不踢线）。

### 4.3 迁移与回滚

- M1 在维护窗内完成**全部** schema 切换（建表/回填/改列/重建 PK/UK/触发器/FK/删旧表旧列），旧代码回滚不再支持。
- 回滚唯一路径 = 快照恢复；发布前演练一次，窗口内保留快照直到冒烟通过。
- 基线重置判据（写入 M1 方案）：迁移超时 > 窗口预算 或 校验失败且无法 30 分钟内修复；决策人=用户；重置后需重录凭证、清 Redis 键、重建 slot。

## 5. 决策记录

| 编号 | 结论 |
|---|---|
| D1 | 三类接口各一单表 |
| D2 | TD:MD=1:N 主备（priority） |
| D3 | 映射可空，运行时报错 |
| D4 | 数据源独立页签 |
| D5 | 三类全插件化；无载体不可启用 |
| D6 | 两页签共用 `system_config` |
| D7 | EMT 本期只拆行情，交易待接入 |
| D8 | 币安/欧易行情启用、交易停用待开户 |
| D9 | 进程常驻；enabled 只决定干活/空转；无载体不建进程 |
| D10 | **code 唯一身份**（接口 PK + 8 处引用 + unit/键/API/UI）；不保留 bigint 身份 |
| D11 | 删除=**软删 `retired_at`**（列表隐藏、进程停、code 不复用）；有历史禁物理删 |
| D12 | 部署面（波次/wrapper/pinned/破坏性门）纳入本批 |
| D13 | bar 流键与归属纳入 C4 |
| D14 | 存量含 is_virtual；列/约束一列不漏 |
| D15 | 迁移唯一路线=离线窗口；M1 一次切完；不做双写 |
| D16 | 回滚=快照恢复；基线重置为授权兜底（需判据与决策人） |

## 6. 契约（C0）

| 编号 | 契约 | 内容 |
|---|---|---|
| C1 身份 | code | `varchar(32) COLLATE "C"`；正则 `^[a-z][a-z0-9-]{1,30}$`；前缀 CHECK（md-/td-/ds-）；不可改 |
| C2 数据 | 三表+映射+租约 | 三表 code PK + 列归属表（见 §6.1）；`quote_account_id` 由 `td_md_map` 取代（M1 删）；业务表 `account_code` TEXT FK→trade_interface；`account_permission` 锚 `login_key`（物理账号权限，不随 TD 行）；`data_source_usage.interface_id`→code；`td_md_map(td_code,md_code,priority)`；`provider_session_slot(login_key,provider,role,terminal_id)` |
| C3 运行 | 状态机 | `待接入(无载体)/运行中·空闲/运行中·工作中/已停止(异常)/已退役`；配置轮询 ≤5s；注册有载体即拉起并 enable；删除=软删+先停 |
| C4 键与流 | unit/Redis | **唯一键族**：unit `quant-md-hub@<md_code>` / `quant-td-exec@<td_code>`；心跳 `iface:hb:<md|td>:<code>`（含 `enabled/working/subs/bars/reset_cnt`，替代 `quant:hb:md-hub:*` 与 `td:exec:*`）；`hub:latest_tick:<md_code>:<sym>`、`hub:latest_tick:pub:<sym>`（公共快照，跨接口共享故不带 code）、`hub:bars:<md_code>:<sym>`、`td:cmd:<td_code>`、`risk:cmd:result:<req_id>`；消息 account=MD code |
| C5 API | 端点/错误码 | `/api/md-interfaces`、`/api/td-interfaces`、`/{code}/mappings`；错误码分四类不得混用：`MAPPING_MISSING`（无映射）/`NO_QUOTE`（有映射无行情）/`PROVIDER_UNSUPPORTED`（无载体）/`IFACE_IDLE`（空闲下发）+`CODE_CONFLICT`/`IFACE_BUSY`；入参/出参 account 一律 code（旧 int 拒绝） |
| C6 插件 | manifest | provider 可声明 md/td 双角色。**载体定义（M8 前）**=`registry` 插件 ∪ legacy `create_md_gateway`（`md_hub` 的 `_LegacyMdShim` 通路，现网 emt/binance/okx 行情走此）——否则 D8「币安/欧易行情启用」与 M4 两 bug 验收自相矛盾。无载体→不建进程、不可启用；M8/P7 销账后只认 registry |
| C7 部署 | 波次/wrapper | 波次取在跑实例；dbro/hbcheck **按 marker 二选一（新/旧），未知 rc=1**；破坏性门参数；pinned 覆盖 code 实例 |

### 6.1 列归属表（M1 建表/回填唯一依据）

| 旧 `trading_account` 列 | market_interface | trade_interface | data_source | 处置 |
|---|---|---|---|---|
| id | — | — | — | 不再作为身份；新表主键=code |
| name/provider/market/params/credentials_encrypted/impl/enabled/position/created_at/updated_at | ✓ | ✓ | ✓ | 原样复制 |
| account_key | ✓ | ✓ | 视原行 | 物理账号语义键；同物理账号 md/td 相同 |
| login_key（新） | ✓ | ✓ | — | 凭证/账号指纹派生；租约与权限锚点 |
| exchanges | ✓ | — | — | 行情侧市场范围 |
| capabilities | — | — | — | **不落新表**：由接口类型+manifest 派生；迁移时仅用于分流 |
| quote_account_id | — | — | — | 由 `td_md_map` 取代，M1 删除 |
| is_virtual | — | ✓ | — | 纸交易虚拟账号属于交易接口 |

**引用归属**：`live_task/position_snapshot/position_refresh/account_snapshot/order_log/trade_log` → `trade_interface(code)`；`account_permission` → `login_key`（物理账号权限，不随 TD 行增删）；`data_source_usage.interface_id` → `data_source(code)`。

**订阅辅助表**：`hub_transient_subs` 增 `md_code`（+market）与 TTL 唯一键；`hub_shadow_symbols` 改为带 `md_code/market` 的表（或键带前缀），禁止全局符号键。
## 7. 模块

| 模块 | 范围 | 依赖 | 验收 | 不做什么 |
|---|---|---|---|---|
| M0 契约 | C1–C7 文档 + §6.1 列归属表 + 常量/正则单测 + 键名唯一性测试 | — | 常量/正则/键名测试当场绿；schema 内省断言移交 M1（同一套断言） | 不写业务 |
| M1 基线迁移 | 三表+映射+租约重建；8 处身份回填；PK/UK/触发器/FK 重建；删旧表旧列；`hub_transient_subs` 加 md_code/market；备份/恢复 runbook（路径/保留/命令/耗时/失败阈值） | M0 | 备份→迁移→冒烟；行数/FK/触发器断言；快照恢复演练（记录耗时） | 不做双写 |
| M2 接口后端 | 三实体 CRUD、code 校验、映射、能力派生校验、审计、软删；**旧消费者改接**（`routes/{trading,risk,chat,mgmt}` 直读 `trading_account` 处 → code；`scheduler`/`strategy` 读点同步） | M1 | curl 全链 + 冲突码 + 软删隐藏；`grep -rn "FROM trading_account" server/src` 为零 | 不做 UI/进程 |
| M3 进程与部署 | 期望=有载体接口；注册即 enable+start；enabled 干活/空转；**凭证/provider 变更检测（exit 9 重建）**；释放 slot；懒加载；删除先停；波次/wrapper | M2 | 四值状态可复现；停用无连接；改凭证后自动重建；code unit 正常；发布+恢复演练 | 不做订阅模型 |
| M4 MD 数据面 | 订阅按接口/provider/market；映射解析（`MAPPING_MISSING`/`NO_QUOTE` 分码）；bar 归属；data_license；**两 bug（含 `strategy_framework/md_gateway.py` 的 `_SWAP_OKX` 生成点）**；心跳加 `subs/bars/reset_cnt` | M3 | OKX/币安断言（reset_cnt/bars 可观测）；worker 正常收 bar；取价日志含 `quote_key` | 不做 TD 执行 |
| M5 TD 执行面 | 映射消费（1:N 主备）、取价降级、空闲不登录、working | M4 | 映射切换生效；空映射明确报错 | 不做 UI |
| M6a 两页签（**并入原子单元**） | 集成中心两页签 + 类型化表单 + AccountsCard 落位 | M2 | CRUD/入口计数 + build 门 | 不含映射编辑（M6b） |
| M6b 映射与状态 | 映射编辑器 + 四值状态卡 | M5 | 主备配置 + 状态正确 | 不含监控规则（M7） |
| M7 观测 | 接口卡片 + 三条告警 | M4/M5 | 停进程/无映射/订阅失败各出一次告警 | 不新建框架 |
| M8 收口 | wrapper code-only、删残留键读、文档同步（D26 v6/A02/D25/D32/A06/D12） | M1–M7 | 全量绿 + pinned 单版 + 旧键零读 | 不改语义 |

### 7.4 开发顺序与上线单元
- 开发顺序：M0 → M1 → M2 → M3 →（M4 → M5）→ M6a → M6b → M7 → M8；M6a 可与 M5 并行。
- **上线单元（重要）**：身份/schema 变更不可拆分——**M1–M5 + M2 旧消费者改接 + wrapper 装位 + collector/readyz 新键可读 + M6a 最小两页签，同一次维护窗同一 release**。M6b 映射编辑器 / M7 告警规则 / M8 收口可后续分批。开发仍是小步，发布是原子的；不存在「先上 M1 再上 M2」。

## 8. 状态
- 需求冻结 v5；无待确认项。
- 文档树已按本需求同步修订（A02/D25/D26 v6/D32/A05/A06/CLAUDE/D12/模块契约），旧口径不再保留。
- 方案阶段细化：迁移窗口预算/锁/超时、备份恢复 runbook、基线重置判据与决策人、login_key 派生规则、wrapper 装位顺序、无载体行 UI 文案。
- 跨模块约定（不变式/边界/上线单元/E2E/失败判据）：`flow/方案/批189-总体实施架构.md`。
- 下一步：M0 契约 + 可执行断言（C1–C7 唯一真源在 M0，本文件只引用）。

