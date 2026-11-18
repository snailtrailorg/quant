# 批189 · TD/MD 接口拆分 —— 现状调研与需求（冻结稿 v2）

> v2 说明：按双盲审（代理A/B）P0 修订后重写，不留补丁。审查报告见 `flow/审查/批189-需求双盲-代理A.md` / `-代理B.md`，综合裁决见 `flow/审查/批189-需求双盲-综合裁决.md`。
> 性质：需求冻结稿（未含实现方案）。下一步按 §7 模块逐个出方案。

## 1. 需求（用户口径）

1. 修两个现网毛病：OKX hub 订阅 A 股标的（`600000_SWAP_OKX`）、币安 hub websocket 反复 reset。
2. TD/MD 彻底解耦：同一券商/交易所也分成「行情接口」「交易接口」两条配置；集成中心拆两个页签；能力集与配置页按类型定义；TD→MD 映射同步调整。
3. 进程随接口存在：接口注册后进程常驻；**配置 `enabled` 只决定干活/空转，不决定 systemd 起停**（用户 2026-11-18 明确）。
4. 三类接口（数据源/行情/交易）全部插件化；不允许「有能力声明、无插件载体」的接口可启用。
5. 小步交付：契约先行，模块化，每步完整、不留尾巴、边界清晰。

## 2. 现状（代码核对）

### 2.1 数据模型

- `trading_account`（0116 expand；0122 删旧 `external_interface`）一条行同时承载 MD+TD：`id BIGSERIAL, name, provider, market, credentials_encrypted, params, capabilities[], position, enabled, account_key, impl, quote_account_id, is_virtual, exchanges, created_at, updated_at`。
- 存量行：`1 中泰XTP测试(xtp/astock)`、`3 东财EMT测试(emt_emq)`、`4 币安(binance_perp)`、`5 欧易(okx_perp)`，**另有虚拟账户行（`is_virtual=true`，0127 纸交易预算隔离）**——拆分枚举必须包含，不能只按 `capabilities` 取。
- 域能力（`quant_common/markets.py:161`）：`data_source={hist_quote,ref_data,inst_event}`，`trading_account={rt_quote,trading}`；测试守门要求两集互斥且并集=全集（`tests/test_markets_registry`）。
- 7 张业务表 FK → `trading_account.id`：`live_task / position_snapshot / position_refresh / account_snapshot / order_log / trade_log / account_permission`。
- `provider_session_slot` PK = `(account_id, provider, terminal_id)`，**无 role 列**；同物理账号拆两行后有终端号互抢风险。
- `trading_account.quote_account_id`（0191，回填=id）仅 td_exec/collector 消费，**前端无入口**。

### 2.2 身份与硬编码（拆分必须处理）

- 数字 id 被当作身份硬编码：`md_hub/main.py:168-175`（ACCOUNT_ID 非数字 exit 78）、`td_exec/main.py:382`、`scheduler/tasks.py:2344/2355`（只停数字实例）、`web_api/routes/system.py`（/readyz 心跳集数字过滤）、`deploy/wrappers/quant-hbcheck`（数字后缀）、`health_monitor/collector.py:249`（isdigit + int）。
- bar 数据面：`hub:bars:{account_id}:{symbol}`（`data_platform/databus.py:225`、`hub_worker.py:98`），消息内 `account_id` 与 worker 的 hub 心跳守卫同源校验（`hub_worker.py:108-116,378-382`）——MD 换 id 会导致 worker 拒收全部 bar、`buy_ok` 恒 False。
- 订阅真相源四源（`md_hub/main.py::_desired_symbols`）：running live_task 全账号标的、`hub_shadow_symbols`（全局）、`hub_transient_subs`（全局）、持仓（仅按本行 id 过滤）→ **A 股标的灌进加密 hub**，即两个 bug 的根因。
- `data_license` 授权门按本行 `trading_account.params` 读（`data_platform/subscription.py`）——MD 换 id 后查不到，异常被吞→白名单静默失效。
- 部署面：`quant-dbro`/`quant-hbcheck` 固定两态选表、不随 release 版本化；hub/td 波次由 DB id 拼 unit（`release.yml`）；阶段 8 `quant-pinned` 枚举全部 `quant-*`。

### 2.3 进程与监控

- `quant-md-hub@<id>` / `quant-td-exec@<id>`（ACCOUNT_ID=%i）；SA4 期望=hub: enabled∩MD providers，exec: `enabled_exec_accounts()`；L3 停非期望数字实例。
- 监控：hub 心跳 `quant:hb:md-hub:<id>`；td_exec TTL `td:exec:<id>`；卡片按账号 id。

### 2.4 两个 bug

- 共同根因：订阅源未按「接口/provider/market」隔离（见 2.2）。OKX 网关把裸码翻译为 `<sym>_SWAP_OKX` 订阅失败；币安网关对非法 symbol 断链→重连循环（`subs=1/bars=0`）。
- 修复归属：M4（订阅模型重做）一次到位；不做临时补丁。

## 3. 目标需求（可验收）

### R1 三类接口实体
- 行情接口、交易接口、数据源三类独立记录；同券商/交易所拆两行。
- 能力由「接口类型 + provider manifest」派生，**页面只读展示**；写侧校验改为「接口类型 ⊆ provider 声明」。
- 验收：交易接口页面不出现行情能力；越域保存返回 400。

### R2 集成中心两页签
- 「交易账号」拆为「行情接口」「交易接口」；数据源页签保留（D4）。
- 复用现有 `InterfacesCard`（kind 参数）与 `ColumnSettings` 习惯，列集按类型裁剪。
- 验收：两页签互不串行；各自 CRUD/排序/连接测试可用。

### R3 进程常驻与工作态（按 D9）
- 接口注册 → 进程**常驻启动**（systemd active），不因 `enabled=false` 而停。
- `enabled=true` → 建会话/订阅/执行（工作中）；`false` → **不建券商连接、不占 terminal、不订阅**（空转）。
- **无插件载体的 provider 不建进程**（如 EMT 交易）：行可保存但标「待接入」，不进期望表。
- 状态三值：`运行中·工作中` / `运行中·空闲` / `已停止（异常）`；由心跳 `enabled/working` + unit 状态派生。
- 验收（唯一口径）：切 enabled → 5s 内心跳 `working` 翻转；停用后 `ss` 无券商连接、`systemctl is-active` **仍为 active**。

### R4 TD→MD 映射（1:N 主备）
- 映射表 `td_md_map(td_interface_id, md_interface_id, priority)`；priority 唯一决定主备（**不设 is_primary**）。
- 允许留空；留空时清仓/取价返回 `MAPPING_MISSING`（明确错误+页面标黄）。
- 主接口取价失败按 priority 降级；映射变更热生效（≤5s）。
- 验收：改映射后清仓日志显示新 MD id；空映射返回明确错误码。

### R5 存量迁移
- 枚举全部存量行（含 `is_virtual` 虚拟账户）与全部列（`impl/account_key/position/quote_account_id/exchanges` 等），给出旧列→新表列映射表，一列不漏。
- **expand 期旧表仍可写**：不采用「可更新视图」冒充兼容层（多表并集不可自动更新）；采用旧表保留 + 双写/同步，M1 与 M2 **同批上产**。
- 历史交易行：TD 沿用旧 id，7 张 FK 与历史数据零重写；MD/DS 新行新 id。
- 迁移加行数/FK 计数断言；破坏性步骤（contract）单独后续批次，上版代码可回滚。

### R6 两个 bug 的验收
- OKX：1 小时内无 `symbol not found: *_SWAP_OKX`；
- 币安：连续 10 分钟无 `Connection reset`，`bars` 递增（或该接口停用不建会话）。

### R7 观测
- 接口维度卡片（行情：连接/订阅/bars 时间/重连次数；交易：进程/会话/映射/持仓年龄）；
- 告警 `iface_down` / `subscription_fail` / `mapping_missing`；只对「有载体且 enabled」的接口判活。

### R8 部署与回滚（审查新增）
- 发布/回滚波次必须覆盖 MD/TD 实例：由「在跑实例枚举」或 DB 新表派生，不再按旧表 id 拼接；
- wrapper（dbro/hbcheck）需三态选表（旧表/新表/并存）并先装位后发布；回滚目标兼容矩阵进方案；
- 验收：一次 staging 彩排 + prod 发布 + 回滚演练，pinned 单版。

## 4. 影响范围

### 4.1 消费者台账（必须逐点核对，审查补）

| 面 | 消费者（file:line 见审查报告） |
|---|---|
| 身份/数字假设 | md_hub 入口、td_exec 入口、SA4 停实例、/readyz、collector、quant-hbcheck、risk exposure、deploy 波次 |
| bar 数据面 | databus/hub_worker 流键、消息 account_id、worker hub 守卫、回放 |
| 订阅 | `_desired_symbols` 四源、data_license、transient/shadow 表 |
| 取价 | td_exec quote 解析、`hub:latest_tick` 读写、清仓降级 |
| 展示 | 集成中心两页签、监控卡片、紧急处置页 execAlive、系统配置页 |
| 旁路脚本 | `scripts/verify_p3_plugin_lifecycle.py`、`scripts/test_emt_cancel.py`、`server/scripts/verify_integration.py`、`data_platform/stock_detail.py` |
| 部署 | `deploy/playbooks/{release,rollback-tasks}.yml`、`deploy/wrappers/{quant-dbro,quant-hbcheck}`、A06 |

### 4.2 迁移形态

- expand：建三表 + 映射 + 旧表双写/同步；旧代码仍以旧表为主；新代码读新表。
- 切换：读侧切新表；进程 unit/键仍用**数字 id**（沿用现有全部数字假设）；`code` 只用于 UI/API/日志展示。
- contract：删旧列/旧表兼容、清 slot 旧租约、删数字硬编码兜底、文档收口。

### 4.3 身份规划（审查修订 D10）

- **主键保持 bigint 自增**，三表各自序列（不跨表共享、不重编号历史）。
- **不做数值分段**：分段与历史 id（1/3/4/5）及 CHECK 冲突，且改 7 FK 会破回滚；类型可读性由 `code` 前缀承担。
- 新增 `code TEXT`（`md-*/td-*/ds-*`，全局唯一、不可改、页面可读），用于 UI/API/日志；**unit 与 Redis 键继续用数字 id**（避免 6+ 处数字假设与 hbcheck/readyz/pinned 连带改造）。
- 同一 provider 多接口：用户自定义后缀（`md-xtp-test`），冲突提示改名，不自动编号。

## 5. 决策记录

| 编号 | 结论 |
|---|---|
| D1 | 行情/交易/数据源各一单表 |
| D2 | TD:MD = 1:N 主备（仅 priority） |
| D3 | 映射允许留空，运行时报错 |
| D4 | 数据源不并入行情接口 |
| D5 | 三类接口全插件化；无载体不得启用 |
| D6 | 两页签共用 `system_config` |
| D7 | EMT 本期只拆行情，交易接口「待接入」（无载体→不建进程） |
| D8 | 币安/欧易：行情启用；交易行停用待开户（无载体→不建进程） |
| D9 | 进程常驻；enabled 只决定干活/空转；无载体不建进程；UI 三值 |
| D10 | bigint 主键 + 不做数值分段 + code 仅展示；unit/键用数字 id（v2 修订） |
| D11 | 删除=先停进程确认退出→删行；停不下来拒绝删除 |
| D12 | 部署波次/wrapper/pinned 纳入本次范围，M1/M3 验收含真实发布 |
| D13 | bar 流键与消息归属纳入 C4，M4 必须保证 worker 不断流 |
| D14 | 存量行含 is_virtual；旧列一列不漏（C2 列映射表） |
| D15 | expand 期旧表可写（双写/同步），不用可更新视图；M1+M2 同批 |

## 6. 契约（C0 冻结物）

| 编号 | 契约 | 内容 |
|---|---|---|
| C1 身份 | code | `^[a-z][a-z0-9-]{1,30}$`；全局唯一；不可改；前缀 md-/td-/ds-；仅 UI/API/日志展示 |
| C1b 主键 | 数字 id | 三表各自 bigint 自增；历史 TD 保留旧号；unit/键用数字 id |
| C2 数据 | 三表 + 映射 | 列=旧表全列映射（含 impl/account_key/position/quote_account_id/is_virtual/exchanges）；`td_md_map(td_id,md_id,priority)` 双 FK + ON DELETE；能力由 manifest 派生 |
| C3 运行 | 状态机 | `registered → running(idle) → working(enabled) → idle → deleting → stopped`；配置轮询 ≤5s；无载体不建进程；删除先停 |
| C4 键与流 | unit/Redis | unit `@<id>`；心跳 `iface:hb:{md|td}:<id>` 字段含 `enabled/working`；数据键 `hub:latest_tick:<md_id>`、`hub:bars:<md_id>:<symbol>`、`td:cmd:<td_id>`、`td:exec:<td_id>`；消息 account_id=MD id，worker 按映射解析 |
| C5 API | 端点/错误码 | `/api/md-interfaces`、`/api/td-interfaces`、`/{code}/mappings`；错误码 `MAPPING_MISSING/PROVIDER_UNSUPPORTED/CODE_CONFLICT/IFACE_BUSY` |
| C6 插件 | manifest | 一 provider 可声明双角色（md/td 各一组 port_caps）；无角色声明→不建该类型进程 |
| C7 部署 | 波次/wrapper | 发布/回滚波次取在跑实例；dbro/hbcheck 三态选表；pinned 断言覆盖新实例 |

## 7. 模块拆分

| 模块 | 范围 | 依赖 | 验收 | 不做什么 |
|---|---|---|---|---|
| M0 契约冻结 | C1–C7 文档 + 契约测试骨架 | — | 测试骨架红/绿可控 | 不写业务 |
| M1 三表 expand | 迁移建三表+映射+双写/同步（旧表可写）；存量全量迁移（含虚拟账户） | M0 | 迁移往返 + 旧代码可建/改接口 + FK 计数断言 | 不切读路径 |
| M2 接口后端 | 三实体 CRUD、code 校验、映射 CRUD、能力派生校验、审计 | M1 | curl 建/改/查/删 + 冲突码 | 不做 UI/进程 |
| M3 进程与部署 | 期望=已注册∩载体；enabled 干活/空转；空闲释放 slot；懒加载；删除先停；发布波次/ wrapper 三态 | M2 | 切 enabled 5s 内 working 翻转；停用无券商连接；一次发布+回滚演练 | 不做订阅模型 |
| M4 MD 数据面 | 订阅按「MD 接口+provider+market」隔离；持仓/live_task 经映射反查；bar 流归属；data_license 回落；两个 bug | M3 | OKX 1h 无错订阅；币安 10min 稳定且 bars 递增；worker 正常收 bar | 不做 TD |
| M5 TD 执行面 | 映射解析 1:N、取价降级、MAPPING_MISSING、空闲不登录、心跳 working | M4 | 改映射后取价切到新 MD；空映射明确报错 | 不做 UI |
| M6a 两页签 | 集成中心两页签 + 类型化表单（复用 InterfacesCard/ColumnSettings） | M2 | 两页签 CRUD 全通 + build 门绿 | 不含映射编辑 |
| M6b 映射与状态 | 映射编辑器 + 三值状态卡 | M5/M7 | 页面配主备映射；状态三值正确 | 不含监控规则 |
| M7 观测 | 接口维度卡片 + 三条告警规则 | M3 | 停进程一个周期内告警；working 可见 | 不新建框架 |
| M8 contract | 删兼容/双写、清 slot、删数字兜底、文档重写与归档 | M1–M7 | 全量绿 + prod pinned 单版 | 不改语义 |

### 7.4 顺序与并行

- 串行：M0 → M1 → M2 → M3 →（M4 → M5）→ M6a → M6b → M7 → M8。
- M1/M2 必须同批（双写期旧表可写）；M5 依赖 M4（键与归属）；M6a 可与 M5 并行，M6b 等 M5。

## 8. 待确认

1. **D10 v2**：数值不分段、unit/键继续用数字 id、code 仅展示 —— 与你原设想「分段/前缀做身份」有差异（审查显示 code 进 unit/键会连带 6+ 处数字硬闸与 hbcheck/readyz/pinned）。确认采用 v2，还是坚持 code 做身份并接受额外兼容层？
2. **无载体接口**（EMT 交易）行可保存但不建进程、标「待接入」——确认。

