# 多市场混合量化交易平台 · 协作约定(Claude Code / Codex 共用入口)

> `AGENTS.md` 软链到本文件，两个工具读同一份。**真相源在文件里，不在对话里。**
> 本文件是**运行时合同**：精要规则 + 约束 + 指针。完整详规在 `flow/规范/`。
> 项目记忆在 `~/.claude/projects/-home-bernard-Projects-Quantitative/memory/`（持久化，跨会话）。

## 目录地图

- `flow/` — 控制层（项目"怎么跑"）：章程 / 计划 / 进展 / 决策 / 踩坑 / 任务 / 规范
- `docs/` — 内容层（项目"做出什么"）：
  - `docs/architecture/`（00-总体设计 ～ 11-feishu-lark，12 份架构文档 + 接口契约 + 模块契约 13 份）
- `server/` - 后端（`src/` Python 3.10 代码 + `scripts/init-seed.sql` + `systemd/` + `requirements.txt` + `.env` + `venv/`）。本地开发 + 部署源，整体 rsync
- `web/` - 前端（Vue3 + Vite，原 `src/web_ui/`）。`npm run build` 后部署 `dist/`
- `scripts/` - 开发机部署工具（`deploy-*.sh`/`quant-deploy.sh`）+ 本地 dev 脚本（`dev-init-db.sh`/`dev-init-valkey.sh`/`verify.sh`）。**不传服务器**
- **判据**：协调/推进项目的 → `flow/`；要交付的内容 → `docs/`（知识/文档）或 `server/src/`（代码）

## 开工前必读

1. `flow/charter.md` — 目标 / 范围 / 约束 / 成功标准
2. `flow/plan.md` — 当前计划（**契约**，未确认不要偏离）
3. `flow/进展.md` **顶部一条** — 上一棒交接棒（做了啥 / 产出路径 / 下一步）
4. `flow/待办.md` - 当前待办跟踪表（**单一真相源**，进展/decisions 不再重复列待办）
5. 项目记忆: `~/.claude/projects/-home-bernard-Projects-Quantitative/memory/MEMORY.md`（跨会话持久化，含所有关键决策与选型）

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
- **策略实盘化架构**：每策略独立子进程（systemd `quant-strategy@<id>`）+ 独立 vnpy MainEngine + XtpGateway 实时驱动（tick->BarGenerator->on_bar）+ XTPAdapter 下单。取 vnpy Gateway 弃全局 MainEngine。回测走自建 BacktestEngine（PG 历史 bar）。详见记忆 strategy-live-architecture。

### 协作约束
- **产出落文件**，不留在对话里。
- 先 plan 后 act，计划即契约；要改先改 `flow/plan.md`。
- 一会话一焦点。
- 产物本身是真相唯一来源；文档只补"看产物看不出的为什么"。
- 解决问题从根本上解，不打补丁。
- 进展条/交接只带「指针 + 增量」，不重抄内容。
- **联网核实**走本机 `spe curl`（WebSearch 不可用，返回幻觉；WebFetch 域安全校验后端不通）。
- **多语言国际化（i18n）**：Web 前端按浏览器语言自动切换中/英文；LLM 回复按输入语言自然回复（2026-08-07 移除 lang 注入与飞书 per-机器人 lang 配置，简化设置）；日志统一英文。
- **待办自包含**：新待办按 `docs/任务/<id>.md`（`flow/规范/任务模板.md` 8 字段）写，做任务时只读「任务文件 + `docs/architecture/接口契约.md` + 本模块契约 `docs/architecture/模块契约/<module>.md`」，零代码阅读。硬约束：限定范围 ≤3 文件 + 参考 ≤2 份。前期写文档成本换后续每任务顺畅。A2 端到端验证成功（2026-08-09，66 单测全过）。任务文件历史在 `docs/obsolete/`。
### 外部待确认 gate（不阻塞开发，但影响实盘）
- 中泰 XTP 门槛/品种放行/费率（用户问客户经理）
- Tushare 积分是否到 2000（分钟线硬门槛，但用户说一次性购买可接受）

## 文档维护（精要）

出现新的设计哲学/心智模型/方向、结构或契约调整、长期约定、需持续参考的外部资料 → 主动**提议**修改点等确认，不擅自写。只沉淀"看产物看不出的为什么"；就近写；保持精简。

完整规范：`flow/规范/文档维护SOP.md`。

## 详规索引

- 工作流程（五段循环/进展日志接力/评审）：`flow/规范/工作流程.md`
- 文档自检 hook：`flow/规范/hook机制.md`
- 任务模板（待办自包含写法规范，8 字段 + mock 库）：`flow/规范/任务模板.md`
- 架构设计：`docs/architecture/00-总体设计.md`（总体设计 10 节，14 份参考文档已合并）
- 接口契约字典（跨模块签名 + 数据结构，任务自包含基础）：`docs/architecture/接口契约.md`
- 模块契约（逐模块 public API + 依赖 + 被调 + 读写表）：`docs/architecture/模块契约/`（13 份全写完）
- 本地开发部署（一键脚本 + 排错）：`scripts/LOCAL-DEPLOY.md`（用 `bash scripts/dev-start.sh start`，不要手动起服务）

## 项目知识（durable，随项目积累 ↓）

全局记忆路径：`~/.claude/projects/-home-bernard-Projects-quant/memory/MEMORY.md`
当前 20 条记忆覆盖：联网限制 / 券商选型 / 数据回测层 / RBAC / 部署 / AI 层 / 项目状态 / 本地启动 / XTP SDK / 实盘开关 / 策略实盘化 / 策略体系 / 回测可视化 / 飞书 / 飞书400bug / 会话交接 / 平台化 / full-subagent / 自包含任务文档。
