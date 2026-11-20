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
- 存量行（2026-11-19 prod API 实测）：`1 XTP / 3 EMT / 4 Binance / 5 OKX` 共 4 行；**`account_key` 4 行全为 NULL**；四行 `capabilities` 均为 `[rt_quote, trading]`；凭证 2 有 / 2 无（币安、欧易）。列表 API 过滤 `is_virtual=false`，故虚拟账户存在性**API 不可见**，M1 前须用 DB 查询确认。
- 域能力：`data_source={hist_quote,ref_data,inst_event}`、`trading_account={rt_quote,trading}`（`quant_common/markets.py`；测试要求两集互斥且并集=全集）。
- 身份引用共 **8 处**：7 张业务表（`live_task / position_snapshot / position_refresh / account_snapshot / order_log / trade_log / account_permission`）+ `data_source_usage.interface_id`（0090，bigint，无 FK）。
- `provider_session_slot` PK=`(account_id,provider,terminal_id)`，无 role；`position_snapshot/position_refresh` 主键含 `account_id`；`live_task` 有按 id 判 `is_virtual` 的触发器（0174）；`order_log/trade_log` 的 account FK 是 SET NULL。
- `quote_account_id`（0191）仅 td_exec/collector 消费，前端无入口。

### 2.2 身份硬编码（全部要改）

- `md_hub/main.py:168-175`（ACCOUNT_ID 非数字 exit 78）、`td_exec/main.py:382`、`scheduler/tasks.py:2344/2355`（只停数字实例）、`web_api/routes/system.py`（readyz 数字过滤）、`deploy/wrappers/quant-hbcheck`、`health_monitor/collector.py:249`。
- **现状（旧形状）**：`hub:bars:{account_id}:{symbol}`（databus.py:225、hub_worker.py:98）+ 消息 `account_id` + worker hub 守卫（hub_worker.py:108-116,378-382）。
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

### R3 进程与状态（两轴模型）

- **运行轴（唯一「状态」）**：`运行` / `停止`。停止=异常或人工停机，红色需告警；`working` 只作副标（工作中/空转），**不是状态**。
- **配置轴（不是状态）**：`完整` / `未完成（缺载体 / 缺凭证 / 缺映射）`。未完成的行可以存在、可以保存，但**不可启用**；以角标+原因呈现，不占状态位。
- **允许草稿**：创建时可只填一部分、`enabled=false`；后续补全再启用。**启用是唯一校验点**——缺载体回 `PROVIDER_UNSUPPORTED`、缺凭证回 `CREDENTIALS_INCOMPLETE`、缺映射回 `MAPPING_MISSING`，启用失败不改 `enabled`。
- **不变式**：`enabled=true` ⇒ 配置完整。已启用的行被改成不完整 → **保存即拒**（400），不允许“在线降级成半配置”。
- 进程：有载体→常驻；`enabled` 只决定干活/空转，不决定 systemd 起停。停用验收：`working=false`、`ss` 无券商连接、`is-active` 仍 active。

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
- **窗内（上产判定）**：OKX 零 `*_SWAP_OKX`、币安零 reset 且 bars 递增（10 min 窗，或该接口停用不建会话）。
- **上产后巡检（不属上产判定）**：OKX/币安连续 1h 同类断言，靠 `subscription_fail` 告警兜底。

### R7 观测
- 接口维度卡片；告警沿用既有两条 **`hub_hb_lost`/`td_exec_down`**（code 化后继续生效）+ 新增三条 `subscription_fail`/`mapping_missing`/`iface_idle_dispatch`。**判活按「有载体」全部接口**（不看 `enabled`）；`working` 只作工作态展示，不参与存活判定。

### R8 部署与回滚
- 本批 schema 破坏性变更：发布 `-e allow_contract=true -e contract_reason="批189 接口三表拆表"`；**`auto_rollback_disabled` 由 playbook 在「豁免生效」时自动置位，操作者不传**；迁移由 release 阶段 5 自动执行，**不手工跑 alembic**（手工会绕过 DDL 门与留痕）；自动 rollback 不支持。
- 回滚 = 恢复迁移前快照 + 旧版代码；**恢复路径已演练成功（2026-11-19 用户确认），窗口内由用户手动执行**；发布前只做备份产物校验（存在/大小/时间戳）。
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
| Web/IM | routes/{trading,risk,strategy,mgmt,chat}.py 的 account_id 入参/出参；`/api/trades`（trade_log 读口，account_id→code）；`/api/health/components`（td_execs/hubs 卡片读口）；IM 工具参数 |
| 旁路脚本 | scripts/verify_p3_plugin_lifecycle.py、scripts/test_emt_cancel.py、server/scripts/verify_integration.py、data_platform/stock_detail.py |
| 部署 | release.yml/rollback-tasks.yml 波次；quant-dbro/quant-hbcheck；A06 |

### 4.2 身份与租约（v5 定稿）

- **code 唯一身份**：三表主键、8 处引用、unit、Redis 键、API、日志、UI；`varchar(32) COLLATE "C"`，正则 `^[a-z][a-z0-9-]{1,30}$`（≤31 字符），表级 `CHECK (code LIKE 'md-%')` 等。
- **不用 registry**：三类前缀互斥，跨表唯一由表内 UNIQUE + 前缀约束自然成立；减少中心表与写序耦合。
- **唯一索引按表分别建**：`(market, account_key) WHERE account_key IS NOT NULL AND enabled` 每表一份（不再有同表 md/td 撞索引问题；M1 预检各表重复计数）。
- **租约域改为物理登录**：`provider_session_slot(login_key, provider, role, terminal_id, ...)`，PK=`(login_key, provider, role, terminal_id)`；`login_key`=**稳定物理身份** = `provider + account_key`，**禁含凭证/密文摘要**（否则轮换即孤儿租约）。
  - **`account_key` 为空的存量行不得静默回落**（回落成 `provider + code` 会让同账号 md/td 拿到两个不同 login_key，租约失共享、互踢线）：M1 **preflight 必须阻断并列出这些行**，回填来源=凭证里的资金账号或运营提供。
  - **唯一例外**：仅行情且无需凭证的接口（crypto public MD）用 `provider + 'public'`（同市场共享一个号，不占 terminal）。
  - 同券商同账号的 md/td 共享 login_key，MD 用 terminal 1、TD 用 2..99，互不踢线。

### 4.3 迁移与回滚

- M1 在维护窗内完成**全部** schema 切换（建表/回填/改列/重建 PK/UK/触发器/FK/删旧表旧列），旧代码回滚不再支持。
- 回滚唯一路径 = 快照恢复 + 旧代码；路径已演练（用户确认），窗口内保留快照直到冒烟通过。
- 基线重置判据：**数值唯一真源=`批189-总体实施架构.md` §5**（迁移预算 40 min / 修复窗 15 min / 观察窗 10 min，2026-11-18 用户确认）；决策人=用户；重置副作用清单见同节。

## 5. 决策记录

| 编号 | 结论 |
|---|---|
| D1 | 三类接口各一单表 |
| D2 | TD:MD=1:N 主备（priority） |
| D3 | 映射可空，运行时报错 |
| D4 | 数据源独立页签 |
| D5 | 三类全插件化；无载体不可启用 |
| D6 | 两页签**共用权限键、按读写分键**：**GET = `read`**（全项目既有约定，如 mgmt.py:333/380 的旧 data-sources/trading-accounts 列表均 `read`）、**写 = `system_config`**；**不按接口类型拆 md_config/td_config**（原"两页签共用 `system_config`"是简写歧义，非"读写都用 system_config"）。读响应须脱敏（凭证只回 `has_credentials`），故 `read` 安全（2026-11-20 用户裁定，从设计优劣而非成本出发） |
| D7 | EMT 本期只拆行情；交易接口**可建行但无载体**，启用时回 `PROVIDER_UNSUPPORTED`（不建进程） |
| D8 | 币安/欧易行情启用；交易接口可建行但无凭证，启用时回 `CREDENTIALS_INCOMPLETE`（开户后补凭证再启用） |
| D9 | 进程常驻；enabled 只决定干活/空转；无载体不建进程 |
| D10 | **code 唯一身份**（接口 PK + 8 处引用 + unit/键/API/UI）；不保留 bigint 身份 |
| D11 | 删除=**先停进程确认退出 → 物理删行**；有 `live_task` 引用则拒删（`IFACE_BUSY`）；派生数据（快照/映射/slot）同事务清理；`order_log/trade_log` 靠 `ON DELETE SET NULL` 保留流水、不再归属接口；**不设软删**（2026-11-19 用户裁定） |
| D12 | 部署面（波次/wrapper/pinned/破坏性门）纳入本批 |
| D13 | bar 流键与归属纳入 C4 |
| D14 | 存量含 is_virtual；列/约束一列不漏 |
| D15 | 迁移唯一路线=离线窗口；M1 一次切完；不做双写 |
| D16 | 回滚=快照恢复；基线重置为授权兜底（需判据与决策人） |

## 6. 契约（指针）

> C1–C7 与 §6.1 列归属表已迁至 **`flow/方案/批189-M0-契约.md`**（唯一真源，2026-11-19）。
> 本节只保留指针：契约改动走 M0 文件 + 断言，改这里无效。

| 编号 | 主题 | 真源 |
|---|---|---|
| C1 | 身份（code 规则/前缀/不可改） | M0 §1 |
| C2 | 数据（三表/映射/租约/8 处引用/删除行为） | M0 §2 |
| C3 | 运行（两轴 + 启用校验矩阵） | M0 §3 |
| C4 | 键族白名单与退役键 | M0 §4 |
| C5 | API 端点/业务码/HTTP 映射 | M0 §5 |
| C6 | 载体定义（registry 与 legacy 过渡） | M0 §6 |
| C7 | 部署（marker/0192/波次/wrapper） | M0 §7 |
| 列归属 | 旧 trading_account 到三表 | M0 §2.1 |

## 7. 模块

| 模块 | 范围 | 依赖 | 验收 | 不做什么 |
|---|---|---|---|---|
| M0 契约 | C1–C7 文档 + §6.1 列归属表 + 常量/正则单测 + 键名唯一性测试；**钉死 M1 迁移=0192 单文件**；**四项必决**：①code 生成与撞名（`binance_perp`→`md-binance-perp`？同 provider/market 两行如何消歧）②**存量 `account_key` 全空**的回填来源与 preflight 阻断清单（见 §4.2）③现存 `provider_session_slot` 行回填 login_key/role/terminal 规则 ④同 login_key 多 TD 接口的 terminal 2..99 分配器 | — | 常量/正则/键名测试当场绿；schema 内省断言移交 M1；需求 §6/§6.1 改纯指针（同提交） | 不写业务 |
| M1 基线迁移 | 三表+映射+租约重建；8 处身份回填；PK/UK/触发器/FK 重建；删旧表旧列；`hub_transient_subs` 加 md_code/market；备份/恢复 runbook（路径/保留/命令/耗时/失败阈值） | M0 | 备份→迁移→冒烟；行数/FK/触发器断言；备份产物校验（存在/≥900MB/时间戳） | 不做双写 |
| M2 接口后端 | 三实体 CRUD、code 校验、映射、能力派生校验、审计、**删除事务**（拒删有任务引用/清理派生数据/历史 SET NULL）；**全部 SQL 读写点改接**（不枚举文件）；`server/scripts`、`scripts/` 中真读写旧表的运维/回归脚本同步改（历史回放脚本显式豁免） | M1 | curl 全链 + 冲突码 + 删除后行消失且派生数据已清；`grep -rnE '(FROM|JOIN|INTO|UPDATE)[[:space:]]+trading_account\b' server/src server/scripts scripts --include=*.py` 为零（域词常量 `DOMAIN_TRADING` 另行在 M8 改名，不在本行验收） | 不做 UI/进程 |
| M3 进程与部署 | 期望=有载体接口（不看 enabled）；**旧消费者读路径切换（13 文件 + 22 测试）+ grep 零**；注册即 enable+start；enabled 干活/空转（**`get_interface_row` 去掉 `enabled=true` 谓词**，否则停用即 exit 78 重启环）；凭证/provider 变更检测（exit 9）；释放 slot；懒加载；删除先停；波次/wrapper；`td_exec` 登记 LAYER=3；回执/心跳写口下沉 `quant_common`；**心跳读侧全量改接**（`td:exec`→`iface:hb:td`：`routes/risk.py`、`collector`、`monitor`；MD 侧 `quant:hb:md-hub`→`iface:hb:md`：`collector`、`readyz`、`hub_worker`、`run_hub_postverify`）；**删 `legacy_transition` 豁免**；既有两条告警规则 code 化 + `evaluate` fail-loud | M2 | 两轴可复现（运行/停止 + 配置完整度）；停用无连接；改凭证后自动重建；code unit 正常；停一个 hub 下一轮出 `hub_hb_lost`；readyz 在 code 键下为 200；发布 + 备份校验 | 不做订阅模型 |
| M4 MD 数据面 | 订阅按接口/provider/market；映射解析（`MAPPING_MISSING`/`NO_QUOTE` 分码）；bar 归属；data_license；**两 bug（含 `strategy_framework/md_gateway.py` 的 `_SWAP_OKX` 生成点）**；心跳加 `subs/bars/reset_cnt` | M3 | OKX/币安断言（reset_cnt/bars 可观测）；worker 正常收 bar；取价日志含 `quote_key` | 不做 TD 执行 |
| M5 TD 执行面 | 映射消费（1:N 主备）、取价降级、空闲不登录、working；**三处 `td:cmd` 直写点改 code**（`strategy.py` / `scheduler/tasks.py` / `routes/risk.py`）；**dispatch 入口判 enabled/连接态 → `IFACE_IDLE` 短路且不写 order_log**；`hub_quote` 入参由 account_id 改 md_code（三种 `_unavailable` 不合并） | M4 | 映射切换生效；空映射/无行情/空闲下发三码分清；策略单/定时急停/手工单三路可消费 | 不做 UI |
| M6a 两页签（**并入原子单元**） | 集成中心两页签 + 类型化表单 + AccountsCard 落位 + **映射只读列表** + 状态只读卡 | M2 | CRUD/入口计数 + build 门；映射与状态可看 | 映射编辑表单在 M6b（过渡期走 M2 映射 API，见总体架构 §3） |
| M6b 映射与状态 | 映射编辑器 + 两轴状态卡 | M5 | 主备配置 + 状态正确 | 不含监控规则（M7） |
| M7 观测 | 接口卡片 + 新增三条告警（`subscription_fail`/`mapping_missing`/`iface_idle_dispatch`） | M4/M5 | 各出一次告警 | 不新建框架；既有两条规则已在原子单元 code 化 |
| M8 收口 | wrapper code-only、删残留键读、**`DOMAIN_TRADING` 域词改名**、**legacy 载体销账（registry only）**、文档同步（D26 v6/A02/D25/D32/A06/D12） | M1–M7 | 全量绿 + pinned 单版；**旧键可证伪判据**：旧键常量（`HUB_HB_KEY`/`HUB_HB_PATTERN`/`td:exec` 字面量）零定义 + 读点清单逐项核对（不靠正则匹配 f-string 键）+ registry-only | 不改语义 |

### 7.4 开发顺序与上线单元
- 开发顺序：M0 → M1 → M2 → M3 →（M4 → M5）→ M6a → M6b → M7 → M8；M6a 可与 M5 并行。
- **上线单元（重要）**：身份/schema 变更不可拆分——**M1–M5 + M2 全量旧消费者改接 + M3 告警规则 code 化 + wrapper 装位 + M6a（两页签+映射只读+状态只读），同一次维护窗同一 release（仅盘后窗）**。M6b 映射编辑表单 / M7 新增告警 / M8 收口可后续分批。开发仍是小步，发布是原子的；不存在「先上 M1 再上 M2」。

## 8. 状态
- 需求冻结 v5；无待确认项。
- **2026-11-19 回流校准**（用户裁定，防评审扩写偏离）：D7/D8 改为「可建行、**启用时**校验」（缺载体→`PROVIDER_UNSUPPORTED`、缺凭证→`CREDENTIALS_INCOMPLETE`）；D11 改**物理删除**（先停→删行→派生清理，历史 `SET NULL`）；C3 改**两轴**（运行态 2 值 + 配置完整度，`working` 仅副标）；C5 业务码收敛为 7 个，基础设施故障走 503+detail。
- 文档树已按本需求同步修订（A02/D25/D26 v6/D32/A05/A06/CLAUDE/D12/模块契约），旧口径不再保留。
- 方案阶段细化：迁移锁模式与 `statement_timeout`、备份恢复 runbook、wrapper 装位顺序、无载体行 UI 文案。（`login_key`/terminal/code 生成规则已入 M0 必决；时间阈值见总体架构 §5）
- 跨模块约定（不变式/边界/上线单元/E2E/失败判据）：`flow/方案/批189-总体实施架构.md`。
- 下一步：M0 契约 + 可执行断言（C1–C7 唯一真源在 M0，本文件只引用）。

