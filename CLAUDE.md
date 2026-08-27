# 多市场混合量化交易平台 · 协作约定(Claude Code / Codex 共用入口)

> `AGENTS.md` 软链到本文件，两个工具读同一份。**真相源在文件里，不在对话里。**
> 本文件是**运行时合同**：精要规则 + 约束 + 指针。完整详规在 `flow/规范/`。
>
> **分层铁律**（2026-08-19 模块归位，tests/test_layering.py 断言守门）：`quant_common`（层 0 底座，禁业务依赖）← 数据/服务层 ← 应用层 ← 入口层（web_api/feishu_bot 组合根）；下层禁 import 上层（lazy 计入）；共享工具放 `quant_common`、业务逻辑不寄生 HTTP 入口。
> 项目记忆在 `~/.claude/projects/-home-bernard-Projects-quant/memory/MEMORY.md`（持久化，跨会话）。
  - 服务器部署信息：`server-info.md`（IP/OS/路径/分库/备份/密钥）
  - 部署机制：`deploy-mechanism.md`（三权分立/脚本/闸门/安全边界）
  - 服务列表：`server-services.md`（systemd/Polkit/Nginx/日志）

## 目录地图

- `flow/` — 控制层（项目"怎么跑"）：章程 / 计划 / 进展 / 决策 / 踩坑 / 任务 / 规范
- `docs/` — 内容层（项目"做出什么"）：
  - `docs/architecture/`（00-总体设计 ～ 19-IM统一接入，20 份架构文档 + 接口契约 + 模块契约 19 份）（P3 回写 2026-08-20：原文"17 份"实数 18（00～17）；同日 DB 盘点批次新增 18 号后现 19 份）
  - `docs/操作指导/`（面向使用者，索引+因子/策略/回测/实盘四册，server/docs 镜像随 rsync 部署）
  - `docs/reference/`（外部参考资料，Tushare API 文档等，参考用非原创——`README.md` 有索引）
  - `docs/obsolete/`（废弃文档归档，保留历史不删除——`README.md` 有归档清单与替代文档对照）
  - `docs/任务/`（自包含任务文件，做任务时只读任务文件+接口契约+模块契约即可动手）
- `server/` - 后端（`src/` Python 3.10 代码 + `scripts/init-seed.sql` + `scripts/systemd/`（单元与 polkit 规则）+ `requirements.txt` + `.env` + `venv/`）。本地开发 + 部署源，整体 rsync（P3 回写 2026-08-20：systemd 实际在 `scripts/systemd/`，根下无该目录）
- `web/` - 前端（Vue3 + Vite，原 `src/web_ui/`）。`npm run build` 后部署 `dist/`
- `deploy/` - **工件化交付（现行，2026-08-26 起在管生产）**：Ansible playbooks（release/rollback/bootstrap 三剧本八阶段+自动回滚）+ inventory（quant-prod/quant-staging 彩排）+ wrappers（quant-svc 等 9 只特权通道）+ collections vendor + 六场景失败注入。**发布=彩排绿后跑 release.yml**（详见记忆 deploy-mechanism）
- `scripts/` - 旧 bash 部署链（**已退役待封存**，保留一个回滚周期）+ 本地 dev 脚本（`dev-init-db.sh`/`dev-init-valkey.sh`/`dev-start.sh`/`verify.sh`）。**不传服务器**
- **判据**：协调/推进项目的 → `flow/`；要交付的内容 → `docs/`（知识/文档）或 `server/src/`（代码）

## 开工前必读

1. `flow/charter.md` — 目标 / 范围 / 约束 / 成功标准
2. `flow/plan.md` — 当前计划（**契约**，未确认不要偏离）
3. `flow/进展.md` **顶部一条** — 上一棒交接棒（做了啥 / 产出路径 / 下一步）
4. `flow/待办.md` - 当前待办跟踪表（**单一真相源**，进展/decisions 不再重复列待办）
5. 项目记忆: `~/.claude/projects/-home-bernard-Projects-quant/memory/MEMORY.md`（跨会话持久化，含所有关键决策与选型）

## 收工前必做

1. 在 `flow/进展.md` **最上面追加一条进展**（做了什么/为什么/产出路径/问题→解决/下一步），**并把这条同时贴在回复里**——这就是交接棒。
2. 决策追加 `flow/decisions.md`；问题/踩坑追加 `flow/踩坑记录.md`。
3. 文档自检：本轮若动了结构/方向/约定，主动提议更新本文件。

## 核心约束（铁律）

### 平台架构约束
- **实盘三级开关**（AND）：`.env ENABLE_LIVE_TRADING` 总闸 + Web `live_trading_config` 分项（convertible/etf/astock/binance_perp/okx_perp）+ 策略 `enabled`+`backtest_verified`。任一关即拒单（`risk_control.check_order` 前置）。A 股/可转债/ETF 统一走中泰 XTP（`XTPAdapter`），加密走币安/OKX。
- **可转债/ETF 实盘**：走中泰 XTP + vnpy_xtp（Linux 原生），需中泰开户+签协议+资产门槛（待券商确认）。
- **加密合约**：币安/OKX 永续，vnpy 加密网关，低杠杆+逐仓。
- **运行期 AI 只用国内模型**：DeepSeek（主）+ GLM（备），**不接 Claude/OpenAI 运行期**。Claude Code 仅作开发助手。LLM 网关按 `priority` 全局主备容灾（2026-08-07 移除 tier 分级--原 tier 是死代码，6 调用点全 regular）。
- **单系统 RBAC**：Admin/Trader/Analyst/Viewer 四角色，非多租户；多租户需求=售出独立实例。Trader（交易：启停策略/熔断/下单）与 Analyst（研究：策略/回测/数据同步）隔离防误操作。
- **部署 OS**：Alibaba Cloud Linux 3（OpenAnolis, al8/RHEL8 系，内核 5.10.134-19.7.al8）。开发机 Fedora（同 dnf/RPM 系）。
- **回测与实盘 schema 对齐**：数据中台 schema 现在就和未来 XTP 实时行情一致，零迁移。
- **配置驱动非硬编码**：策略因子组合/权重/参数走 Web 配置 + DSL 表达式，每个策略都改代码是错误做法。
- **平台化通用接口**：6 大接口抽象（DataSource/Broker/MessageChannel/Task/RiskRule/LLMProvider），结构接口按通用方向设计，实现可简化--别人通过配置 + 实现接口子类即可接入自己的数据源/通道/AI/工具，不改平台代码。业务菜单保留，管理设置类按通用化设计（2026-08-08 决策，详见 `flow/decisions.md` + 待办 PT1-PT8/PI1-PI5）。

### 技术栈约束
- **Python 版本**：服务器 `120.24.235.98` = 3.11（venv 3.11.13），本地开发机 = 3.10。**不用 3.14**——根因：vnpy 4.4.0 硬 pin `pyside6==6.8.2.1`，该版本 `requires_python <3.14`，3.14 上 pip 装不上 vnpy 4.4.0；resolver 回退 vnpy 4.0.0 与 vnpy_binance 2026.7.23 错配（`Exchange.GLOBAL` 缺失致 import 崩）。纯 Python 依赖（numpy/pandas/psycopg/PySide6 6.11/fastapi/celery 等）在 3.14 全 OK，卡点单一在上游 vnpy 的 PySide6 pin，等 vnpy 放宽即解（2026-08-03 实测）。改版本要严格评估 + 客户确认，不擅动。
- **PostgreSQL 18 + pgvector + Valkey**（Redis 协议兼容）。弃 TimescaleDB / 重型向量库。
- **vnpy 核心 + vnpy_xtp**（交易+行情接口）为第三方成熟组件，不重复造轮；**回测自建 BacktestEngine**（纯 Python，不依赖 vnpy CtaBacktestingEngine）。
- **Schema 版本管理用 alembic**（对齐 safebox）：变更走迁移文件（`alembic revision` + 手写 upgrade/downgrade + `deploy-server.sh migrate`），不手动 ALTER。`init-schema.sql` 保留作手工运维参考。**运行时不再 `CREATE TABLE IF NOT EXISTS`**（2026-08-13 清零，原 30 处全部入迁移 0027）；`db.py` 的 `verify_schema()` 启动时校验表存在并告警，动态表 `bar_{freq}` 保留 `ensure_table()` 但用 `_ensured_tables` 集合避免重复 DDL。
- **策略实盘化架构**：每任务独立子进程（systemd `quant-live-task@{tid}`，live_task 单元模板；旧 `quant-strategy@<id>` 仅为废架构遗留兼容）（P3 回写 2026-08-20 单元名归真）+ 独立 vnpy MainEngine + XtpGateway 实时驱动（tick->BarGenerator->on_bar）+ XTPAdapter 下单。取 vnpy Gateway 弃全局 MainEngine。回测走自建 BacktestEngine（PG 历史 bar）。详见记忆 strategy-live-architecture。

### 协作约束
- **产出落文件**，不留在对话里。
- 先 plan 后 act，计划即契约；要改先改 `flow/plan.md`。
- 一会话一焦点。
- 产物本身是真相唯一来源；文档只补"看产物看不出的为什么"。
- 解决问题从根本上解，不打补丁。
- 进展条/交接只带「指针 + 增量」，不重抄内容。
- **联网核实**走本机 `spe curl`（WebSearch 不可用，返回幻觉；WebFetch 域安全校验后端不通）。
- **多语言国际化（N 语言架构）**：注册表驱动（en 为缺省），加语言=只加条目零逻辑改动。页面按浏览器语言自动切换；条款全语言纵向堆叠；邮件跟操作者界面语言；LLM 按输入语言自然回复。详见记忆 `multilang-architecture`。
- **后端错误码化**：用户流程错误统一 `ApiError(status, CODE, 中文兜底)` → `{detail, code}`；前端 `apiErr(e)` 优先 `err.<CODE>` 本地化。加新码=后端定码+前端 err 命名空间加条目。
- **待办自包含**：新待办按 `docs/任务/<id>.md`（`flow/规范/任务模板.md` 8 字段）写，做任务时只读「任务文件 + `docs/architecture/接口契约.md` + 本模块契约 `docs/architecture/模块契约/<module>.md`」，零代码阅读。硬约束：限定范围 ≤3 文件 + 参考 ≤2 份。
### 外部 gate（状态见 `flow/待办.md` 外部 gate 表，单一真相源；P3 回写 2026-08-20 改已确认态）
- ~~中泰 XTP 门槛/品种放行/费率~~ ✅ 已确认（2026-08-10 用户确认：测试账户能当正式账号用，无差别）
- ~~Tushare 积分是否到 2000~~ ✅ 已确认：积分 200 日线够用；分钟线从 XTP 测试平台自攒（stk_mins 产品包 2000 元/年可选后启）
- 币安/欧易 API ⛔ 未开通（加密实盘待接，不阻塞开发）

## 文档维护（精要）

出现新的设计哲学/心智模型/方向、结构或契约调整、长期约定、需持续参考的外部资料 → 主动**提议**修改点等确认，不擅自写。只沉淀"看产物看不出的为什么"；就近写；保持精简。

完整规范：`flow/规范/文档维护SOP.md`。

## 详规索引

- 工作流程（五段循环/进展日志接力/评审）：`flow/规范/工作流程.md`
- **八步法（强制交付流程，2026-08-26 起）**：`flow/规范/八步法.md`（方案→双盲审→编码→双盲审→单测→提交→部署→集成测试，顺序强制，审核一律独立双盲）
- 运行时架构对标与重构依据：`docs/architecture/20-运行时架构对标与差距分析.md`（三根源）+ `12-实盘稳定性设计.md` §2.9/2.10（批次表）
- 文档自检 hook：`flow/规范/hook机制.md`
- 任务模板（待办自包含写法规范，8 字段 + mock 库）：`flow/规范/任务模板.md`
- **架构文档导航（智能体按需读取入口）**：`docs/architecture/README.md`（"要做什么读什么"场景表 + 六层文档分层 + 状态注记约定）
- 架构设计：`docs/architecture/00-总体设计.md`（总体设计 10 节，14 份参考文档已合并）
- 实盘稳定性：`docs/architecture/12-实盘稳定性设计.md`（风险清单/监控/守则）；稳定性检查台账 `flow/稳定性检查矩阵.md`（F-1~F-59）
- 服务监控（S6 修订）：`docs/architecture/15-服务监控设计.md`（断流不自杀/下单时刻判定/health_monitor 内层+Zabbix@NAS 外层//healthz /readyz /metrics=Prometheus）
- 操作指导书（面向使用者）：`docs/操作指导/`（索引+因子/策略/回测/实盘四册，server/docs 镜像随 rsync 部署）+ Web 内置 `/help`（`/api/help/{topic}`）
- 多频率数据（16 号 v2.1 定稿，影子门禁后实施）：`docs/architecture/16-多频率数据设计.md`（慢路径日线直读+日界沿/快路径分钟；复权逐行因子链；NULL 因子=1.0 降级；盘口 Phase 2）
- 三档数据与详情页（17 号，U 审 21 项）：`docs/architecture/17-三档数据与详情页.md`（2026-08-20 三档 6 项+项 5 选股全上线；剩项 18 监控/项 11 质量/项 4 时点实测；含 U 审裁定与坑）
- 数据库操作规范（18 号，2026-08-21 定稿）：`docs/architecture/18-数据库操作规范.md`（全仓写路径盘点/写路径五规范：executemany+事务禁跨网络+DDL CONCURRENTLY/超时分层 web 10s·同步 60s·idle_tx 5min/长事务告警 R7/pg_stat_activity 诊断钥匙——锁链事件根治）
- IM 统一接入（19 号，2026-08-21 批 1+2 上线）：`docs/architecture/19-IM统一接入设计.md`（IMBotProvider 抽象/im_bot_config+im_bot_users 统一表/凭证异构 JSON/动态 FIELD_SCHEMA 表单/接入向导状态机；接新 IM=实现子类+配置零平台改动）
- 共享行情 Hub（ST7）：`docs/architecture/13-需求书.md` + `14-设计.md` v2（hub=纯数据面单实例 MD，worker=TD-only；Valkey Streams 分发+租约 fencing；影子期 bar_hub/bar_shadow diff 门禁）
- 接口契约字典（跨模块签名 + 数据结构，任务自包含基础）：`docs/architecture/接口契约.md`
- 模块契约（逐模块 public API + 依赖 + 被调 + 读写表）：`docs/architecture/模块契约/`（19 份，2026-08-21 增 web_api）+ im_bot
- 本地开发部署（一键脚本 + 排错）：`scripts/LOCAL-DEPLOY.md`（用 `bash scripts/dev-start.sh start`，不要手动起服务）
- **发布/回滚/彩排**：`deploy/` 目录（现行 Ansible 管道；彩排先行的完整制度见记忆 deploy-mechanism 与 docs/任务/批3-工件化交付.md）

## 项目知识（durable，随项目积累 ↓）

全局记忆索引：`~/.claude/projects/-home-bernard-Projects-quant/memory/MEMORY.md`（每条一行+指向，新增记忆必须同步索引）
