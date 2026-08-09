# 决策日志 (decisions)

> 记"过程决策 + 为什么"。**追加,不删改**——下一棒最值钱的上下文。
> (架构 / 产品级的"为什么"按 `flow/规范/文档维护SOP.md` 进 `CLAUDE.md`;这里记项目怎么推进的过程决策。)

<!-- 模板:
## YYYY-MM-DD · <决策标题>
- 背景:
- 决定:
- 否决的方案 & 原因:
-->

## 2026-08-03 · 飞书机器人扫码接入方案（lark_oapi.register_app 官方 SDK）

- **背景**：用户要飞书配置 DB 化 + 扫码添加机器人+开通权限。评估两版参考文档（旧版猜测端点，升级版用官方 SDK）。
- **决定**：采用升级版--飞书官方 SDK `lark_oapi.register_app`（RFC 8628 设备授权），用户扫码一键创建智能体应用 + 自动返回凭证 + SDK 预置权限。**核实通过**（v1.7.1 register_app 真实存在 + 官方文档背书）
- **架构**：后端服务凭证 `.env` + Celery 异步 register_app + Valkey 状态 + DB `feishu_config` 加密 + Vue3 扫码页 + `/feishu/webhook` 回调
- **否决**：旧版方案（猜测 `oauth/v2/device/authorize` 端点，声称返回 app_secret，违反 OAuth 安全模型，AI 不准确）
- **待确认 4 点**：后端服务凭证有无/存哪/webhook URL/与 LLM 一起 plan
- **参考**：记忆 `feishu-bot-architecture` + `docs/reference/飞书机器人扫码连接完整技术实现方案.md`

## 2026-08-04 · 飞书机器人权限模型：per-机器人角色（机器人=登录账号）

- **背景**：用户澄清"机器人权限"= 系统RBAC角色（Admin/Trader/Analyst/Viewer），不是飞书scope（scope 默认够，飞书后台配，不用改）。per-机器人更灵活：每个机器人=一个登录账号，有权限级别。
- **决定**：
  1. feishu_config 加 `role`（默认 viewer）+ `lang`（默认浏览器语言）+ `description`
  2. per-机器人角色：用这个机器人的人都是该角色（弃 .env LARK_AUTHORIZED_USERS per-user open_id:role）
  3. Web 设置弹窗改 role/lang/description（非行内下拉，明确确认+反馈）
  4. 语言缺省浏览器语言（navigator.language）+ 修改后生效（process_message_async 用 feishu_config.lang 调 LLM）
  5. ws_client on_message 传 fid -> process_message_async 用 fid 查 role+lang -> _filter_tools(role) + LMGateway.chat(lang)
- **否决**：per-user 授权（每个飞书用户独立角色，要单独表管理，复杂）；行内下拉改（易误改无确认）
- **飞书scope**：addons 默认（发消息/收消息/卡片回调），不用改，飞书后台配无 UI

## 2026-08-03 · 策略+回测体系架构定案（四层+ActionSignal+因子缓存+风控覆写+双引擎两阶段）

- **背景**：专家给"双引擎折衷方案"（拖拽+代码框，`docs/reference/量化交易平台策略体系架构设计全景.md`）；同时评估 PTrade 回测可视化参考（`docs/reference/PTradeQuant/screen-shot-description.md`）。定策略体系 + 回测可视化方向。
- **决定**：
  1. **四层架构**（因子/策略/风控/执行）--已有，一致
  2. **ActionSignal 契约**扩展 Signal/Order：volume_type（PERCENT/SHARES/ALL_IN）+ price_type（MARKET/LIMIT）+ order_validity（DAY/GTC）+ extra。支持百分比仓位
  3. **因子层**补参数 Hash 缓存（全局算一次）+ 向量化 DataFrame（严禁逐标循环）
  4. **风控覆写能力**：check_order 不只 reject，还能修正（硬止损/动态止盈/单笔仓位硬顶/可用性校验）。RiskDecision 加 adjusted_signal
  5. **双模式两阶段 + 统一 Python 执行**（2026-08-03 优化修正专家双引擎）：双模式只在输入/存储/展示端，执行层统一一套 Python--DSL 编译成 Python 策略代码 + 代码模式直接 Python，都成 Strategy 子类实例走 `on_bar->ActionSignal->风控->执行`。阶段一配置驱动（待办 #3，DSL 转译）；阶段二代码框（未来，沙箱就绪）。比专家"双引擎两套执行"更优雅（执行一套/回测实盘一致/可审计/AI 生成统一）
  6. **回测可视化**（PTrade 借鉴）：多标签页（收益概述/交易详情/持仓/日志/滚动绩效）+ BacktestResult 扩展指标（α/β/索提诺/信息率/波动率/基准）+ API 分层（overview/trades/positions/logs/metrics）+ backtest_tasks 缓存表 + 导出报告
- **否决**：① 纯代码框（PTrade 模式，违反配置驱动铁律）；② 纯配置永不加代码框（DSL 不够 ML 时受限）；③ Monaco 在线编辑器（配置驱动替代）
- **不违反铁律**：双引擎的代码框是用户层策略代码，非平台架构层改源码
- **参考**：记忆 `strategy-framework-architecture`（权威）；两份 docs/reference

## 2026-08-03 · 策略实盘化架构定案（修正版 B + systemd + XtpGateway 实时驱动）

- **背景**：策略实盘化（待办 #4）要定架构。评审专家推荐 B（独立子进程+自建调度），讨论认同大方向修正一点。参考文档 `docs/reference/VNPY策略调试加载回测相关建议.md`。
- **决定**：
  1. **修正版 B**：每策略独立子进程 = 独立 vnpy MainEngine + XtpGateway + Strategy + XTPAdapter。进程隔离 + 冷重启改参数 + 多资产各自子进程
  2. **不轻量化**：实测 vnpy 核心 60MB + MainEngine 9MB，轻量只省 9MB；PySide6 headless 不引（grep 确认）。用完整 MainEngine（封装齐全 + 与 test_xtp 一致）
  3. **systemd 管理**：`quant-strategy@<id>.service` 模板 + `Restart=always` + `MemoryMax`/`CPUQuota`（cgroup 限资源）+ polkit（Web 控制，待办 #14）
  4. **行情驱动**：XtpGateway 实时订阅 tick -> BarGenerator -> on_bar（实时）；PG 读历史 bar 暖机 + 断线补缺口（混合，非纯 gateway）。回测走自建 BacktestEngine（PG 历史 bar）。on_bar 逻辑回测/实盘一致
  5. **取 vnpy Gateway 弃全局 MainEngine/EventEngine**（每子进程独立实例，不共用）；不用 CtaTemplate（自建 Strategy）；不用 CtaBacktestingEngine（自建 BacktestEngine）
- **否决**：A（vnpy 主引擎共用进程）--进程隔离/热修/多资产时间轴（A股盘中 vs 币圈7×24 事件队列积压）不满足
- **待澄清**：连接配额（N 策略 N 连接中泰）、CPU（4 核 N 进程）、tick vs bar 粒度（可转债 T+0）
- **参考**：记忆 `strategy-live-architecture`（权威详细）；`docs/reference/VNPY策略调试加载回测相关建议.md`（专家原方案）

## 2026-08-03 · 废止 A 股三重只读铁律，A 股进实盘开关

- **背景**：原 charter/CLAUDE.md "A 股三重只读"（代码层注销下单 + AStockReadonlyAdapter.send_order 永久 raise + LLM 网关白名单不含下单工具）。用户决定"去掉铁律，彻底忘了，A 股也要做 Web 开关"--A 股和可转债/ETF 一样能实盘交易，走配置开关控制（非 Agent 自律）。
- **决定**：
  1. 删 `AStockReadonlyAdapter` 类 + `create_adapter` 删 `astock_readonly` 映射 + `__init__.py` 删 import--A 股交易统一走 `XTPAdapter`（中泰 XTP 能交易 A 股股票/可转债/ETF）
  2. `live_trading_config` 加 `astock` 分项（5 项：convertible/etf/astock/binance_perp/okx_perp）
  3. `risk._market_of`：A 股股票(60/00/30) -> `"astock"`（原返回 None 拒单）
  4. `XTPAdapter` 删品种边界 `_check_symbol_allowed`（放开 A 股，原拒 A 股）
  5. LLM `FORBIDDEN_TOOLS` 放开 `place_order`/`cancel_order`（仅留 modify_risk_rule/modify_strategy_params 禁）
  6. CLAUDE.md/charter.md 删"A 股三重只读"铁律段，改"实盘三级开关"（含 astock）
- **否决**：保留 `AStockReadonlyAdapter` 改可下单（类名"Readonly"名不副实，且 A 股走 XTPAdapter 统一通道更干净，弃）。
- **影响**：平台定位变更--A 股从"纯分析不下单"变"可实盘交易（受 astock 分项开关控制）"。DB 无 astock_readonly 策略配置（create_adapter 调用点 0），无需改 DB 数据。

## 2026-08-03 · 实盘下单三级开关 + XTPAdapter 品种边界

- **背景**：用户纠正--测试环境要把所有接口测完整让系统具备能力，"不下单"是将来运行的风险防控开关（配置项），不靠 Agent 自律。
- **决定**：
  1. 三级 AND 开关：`.env ENABLE_LIVE_TRADING`（总闸，生产 false/测试 true，重启生效）AND `live_trading_config` 表 Web 分项（convertible/etf/binance_perp/okx_perp，admin 可配）AND `strategy_config.enabled`+`backtest_verified`（策略级，已有）。任一关即拒单。
  2. `check_order` 开头按 symbol 判 market 查两级开关，未开拒单；A 股股票 market=None 直接拒。
  3. XTPAdapter 品种边界双保险：只放行可转债(11/12)/ETF(51/15/56)，A 股股票 raise PermissionError。配合 AStockReadonlyAdapter 永久 raise（铁律独立于开关）。
  4. Web 端点 `GET/PUT /api/live-trading`，新权限 `live_trading_control`（trader/admin）。
- **否决**：① 单级 .env 开关（不够细，无法按品种/市场单独控制）；② 纯 Web 开关（误操作风险高，无总闸兜底）；③ 按策略分项（已有 strategy_config.enabled，复用即可，不重复）。
- **测试**：`server/scripts/test_xtp_trading.py`，服务器交易时段跑，510050 限价 3.00 买 100 股 ~300 元小金额。

## 2026-08-03 · XTP 连接验证脚本固化到 server/scripts/

- **背景**：本地+服务器双环境 vnpy_xtp 就绪后，需验证 XtpGateway 连中泰测试账户。验证脚本要能上服务器跑（本地出口网络连不到中泰，实盘连接验证必须在服务器）。
- **决定**：验证脚本 `test_xtp_connect.py` 放 `server/scripts/`（随 `server/` rsync 上服务器，`deploy-server.sh` 自动同步），不放项目根 `scripts/`（开发机工具不传服务器，要手动 cp）。
- **结果**：服务器登录成功（账户 100 万 + SZSE 合约推送），本地纯代码层验证通过（连接登录走服务器）。
- **约束**：脚本只 connect + 订阅行情，**绝不下单**（A 股三重只读铁律）。

## 2026-08-03 · 本地手动编译 vnpy_xtp（不走 build-xtp.sh）

- **背景**：本地 venv 3.10 要能跑 vnpy_xtp（cp310）。`build-xtp.sh` 是服务器专用（硬编码 `/data/websites/...` 路径 + `sudo -u quant` + 永久 `ln -sf /usr/bin/python3.11 /bin/python3`），本地跑会误伤系统 python3（Fedora 默认 `/bin/python3→3.14`，改了破坏 dnf）。
- **决定**：本地手动命令编译，**不动 `/bin/python3`**，用 `PATH=venv/bin:$PATH` 让 meson 找到 venv 3.10。前提：系统装 `python3.10-devel`，venv 装 `pybind11 meson ninja meson-python`。
- **命令**：`PATH=venv/bin:$PATH CPATH=vendor/xtp/include LIBRARY_PATH=vendor/xtp/lib LD_LIBRARY_PATH=vendor/xtp/lib venv/bin/pip install --no-build-isolation --config-settings=compile-args=-j1 vnpy_xtp`
- **否决方案**：① 本地跑 `build-xtp.sh`（永久改 `/bin/python3` 破坏 Fedora 系统，弃）；② 临时改 `/bin/python3` symlink 编译后恢复（PATH 方案更干净，弃）。
- **结果**：wheel `cp310-cp310-linux_x86_64.whl` 装入，`from vnpy_xtp import XtpGateway` OK。

## 2026-08-03 · 本地 Python 版本回退 3.10，不用 3.14

- **背景**：本地开发机系统 `python3 = 3.14.6`，原 venv = 3.10.20。为验证两环境兼容性（本地 3.14 / 服务器 3.11 互补），试本地改 3.14。
- **决定**：本地回退 3.10.20（保持原 venv）。CLAUDE.md 技术栈约束改为记录性质——服务器 `120.24.235.98` = 3.11，本地 = 3.10，不用 3.14，改版本要严格评估 + 客户确认。
- **否决方案 & 原因**：
  - 本地 3.14 强装 vnpy 4.4.0 + PySide6 6.11（忽略 vnpy 的 pyside6 pin）：vnpy 4.4.0 代码可能不兼容 PySide6 6.11，运行时崩风险，弃。
  - 本地 3.14 不带 vnpy 全家桶（纯 Python 开发环境）：本地无法跑 vnpy 策略/回测引擎，弃。
  - 本地升 3.11（与服务器同质）：失去互补验证价值，且未试，弃。
- **根因**：vnpy 4.4.0 硬 pin `pyside6==6.8.2.1`（`requires_python <3.14`），详见 `flow/踩坑记录.md` 2026-08-03 条。

## 2026-08-02 · 采纳专家建议（6 项改进）

- **背景**: 专家评审给了一些建议，经分析采纳 6 项（拒绝开放注册/多角色 SaaS 思路/登录设备/数据版本备份/分区表/限流等，违反个人私有化 charter 或过度设计）。
- **决定（6 项）**:
  1. **邀请制用户管理**：admin 邀请（填 email 发邮件）→ 被邀请者自助开通（默认 Viewer）+ 改密码/找回密码 + 邮箱认证。复用 safebox SMTP 实现。
  2. **4 级权限**：Admin/Trader/Analyst/Viewer（原 Operator 拆成 Trader 交易 + Analyst 研究，隔离防误操作）。charter/CLAUDE.md 已改。
  3. **WEB 菜单重构**：4 主菜单（交易工作台/数据分析/系统运维/账户设置），配合 4 级角色动态显示。
  4. **cron 调度 + 交易日日历**：sync_config schedule 改 cron 表达式 + is_trading_day 过滤（alembic 迁移改 schema）。
  5. **SQLAlchemy 连接池**：db.py 加 engine（pool_size=10），各 handler psycopg.connect 改 engine.connect()，**保留裸 SQL 不用 ORM**。
  6. **虚拟滚动 + 懒加载**：标的列表（5534 只）用 el-table-v2（懒加载已实现）。
- **拒绝**：开放注册/邮箱验证码注册/登录设备管理/数据版本备份/分区表/API 限流/邮件验证码（SaaS 思路或过度设计，违反 charter 个人私有化 + 非多租户）。
- **实施顺序**：①4 级权限 → ②SQLAlchemy 连接池 → ③cron 调度 → ④邀请制用户 → ⑤菜单重构 → ⑥虚拟滚动 → ⑦M2 闭环（回测 API/策略表单/因子 API）。
- **待办改进项**（已移至 `flow/待办.md` 跟踪表统一管理，以下为历史记录）:
  1. **回测 API**（POST /api/backtest）- M2 闭环核心，BacktestEngine 就绪只差端点
  2. **策略创建表单**（因子+权重+DSL 编辑器）- 配置驱动铁律落地，当前只有启停
  3. **因子列表 API**（GET /api/factors）- 因子注册表就绪，只差端点
  4. **实盘持仓/盈亏对接** - 当前占位，实盘要真实数据（依赖 XTP/币安网关）
  5. **三账对账 API**（GET /api/reconcile）- 风控闭环需要
  6. **场景串联**（选股→标的池，回测→实盘一键部署）- 打通功能孤岛，M2 方向
  7. **密码改 bcrypt** - ⚠️ auth.py 现用 sha256，requirements 已装 passlib[bcrypt] 没用上
  8. **数据完整性看板** - DataManage 展示每只标的完整性百分比（预期 vs 已有天数）
  9. **Dashboard 量化指标**（PnL/Sharpe/最大回撤）- 总览看板从占位变真实
- **影响**: charter/CLAUDE.md RBAC 三角色→四角色已改。后续 auth.py PERMISSIONS 矩阵 + 前端菜单动态显示要改。

## 2026-07-31 · 引入 alembic schema 版本管理

- **背景**: 当前 schema 分散（CREATE TABLE IF NOT EXISTS 各 handler）无版本管理，变更靠手动 ALTER，部署踩 6 个 schema 坑（audit_log 缺列/sync_log 缺表/bar_1D 缺表+owner/duration_ms）。safebox 已用 alembic。
- **决定**: 引入 alembic，schema 变更走迁移文件，`alembic upgrade head` 幂等升级。
  - `env.py` 同步 psycopg3（quant 无 SQLAlchemy 模型，手写迁移不用 autogenerate），URL 从 `QUANT_DB_URL` 读转 `postgresql+psycopg://`
  - `0001_initial.py` 建 10 表（从 init-schema.sql 转），alembic 用 quant 用户跑 owner 自动 quant
  - `quant-deploy.sh` 加 `migrate` 动作（alembic upgrade head）
  - `init-schema.sql` 保留备用，各 handler 的 CREATE TABLE IF NOT EXISTS 保留兜底
- **后续 schema 变更流程**: `alembic revision -m "xxx"` + 手写 upgrade/downgrade + `deploy-server.sh migrate`，不手动 ALTER
- **影响**: schema 有版本管理可追踪可回滚，根治漂移；和 safebox 对齐工具链

## 2026-07-31 · 部署方案优化（新服务器迁移）

- **背景**: 当前部署踩 6 坑（schema 分散鸡生蛋 / .env db 冲突 / 磁盘无规划 / safebox FLUSHALL 破坏 quant / 状态展示不一致 / 脚本脆弱）。用户要新服务器迁移 + 稳健方便 + 安全底线。
- **决定**:
  1. **集中 init-schema.sql** 建所有业务表（owner=quant），首次部署跑一次，避免鸡生蛋
  2. **init-env.sh** 交互式生成 .env，db4/5/6 隔离 safebox db0
  3. **SYNC_START_DATE** 可配（默认 20100101 全历史约 3GB，磁盘紧张改 2020）
  4. **sync_all_symbols 更新 last_status**（DataManage 页看全量进度）
  5. **safebox-deploy.sh 改两处**：clear-redis FLUSHALL->FLUSHDB -n 0，restart-web restart->reload
  6. **安全模型保持**：michael 操作服务器，bernard sudoers 限定 `sudo -u michael` 跑两个 deploy 脚本
- **数据迁移**: 都重新初始化（safebox 用户数据也重建，最干净）
- **全量起点**: 2010-01-01 全历史（约 3GB，新服务器磁盘 40GB+）
- **影响**: 部署方案见 `DEPLOY.md`，工具 `init-schema.sql` + `init-env.sh` + `quant-deploy.sh`

## 2026-07-26 · 目录重构 server/+web/ 对齐 safebox

- **背景**: 原 `scripts/` 混服务器脚本（init-seed.sql/systemd/）+ 开发机部署工具（deploy-*.sh/quant-deploy.sh），deploy-server.sh 把 deploy-*.sh 误传服务器（多余 + 含路径信息）。`src/` 混后端代码 + 前端 web_ui/，部署要排除 node_modules。
- **决定**: 参考 safebox 重构为三目录：
  - `server/`：后端（src/ + scripts/init-seed.sql + systemd/ + requirements.txt + .env + venv/），整体 rsync 部署
  - `web/`：前端（原 src/web_ui/），npm build 后部署 dist
  - `scripts/`：开发机部署工具（deploy-*.sh/quant-deploy.sh）+ 本地 dev 脚本（init-db.sh/init-valkey.sh/verify.sh），**不传服务器**
- **.env 隔离**: 本地 `server/.env`（trust 免密）vs 远程 `.env`（md5 密码），deploy `--exclude .env` 不覆盖
- **远程结构不变**: PROJECT_PATH/src/+... 与重构前一致，服务器已部署代码无缝；下次 deploy-server.sh 同步新结构，rsync --delete 清理误传的 deploy 脚本
- **影响**: 本地开发启动改 `cd server` / `cd web`；部署脚本简化（一个 deploy 传 server/）；import 路径不变（`src.web_api`，WorkingDirectory=server）

## 2026-07-26 · 生产部署与 SafeBox 共存隔离方案

- **背景**: 项目要部署到与 safebox 同一服务器（snailtrail.org），共用 PG/Redis/Apache。safebox 部署时未考虑后续项目，clear-redis 用 `FLUSHALL`（清所有 db）、restart-web 用 `restart httpd`，会连带破坏 quant。
- **决定**: 用子域名+端口+DB号+库名隔离。
  - 域名 `quant.snailtrail.org`（Apache vhost + certbot 证书）
  - 后端 `:8001`（避开 safebox :8000），3 个 systemd 服务（web-api + celery-worker + celery-beat，模板式 @quant 实例）
  - 复用 safebox 的 PG（quant 库，md5 密码认证，不复用 trust）+ redis6（db2=VALKEY, db3=CELERY，避开 safebox db0）
  - 系统用户 `quant`，部署路径 `/data/websites/snailtrail.org/quant/`
  - 后端 uvicorn 单进程（暂不 gunicorn，本项目有 RiskControl 单例/因子注册表等全局状态，多 worker 有风险）
  - 无 alembic（靠 CREATE TABLE IF NOT EXISTS + startup 钩子建表）
- **隔离铁律**（写进 quant-deploy.sh）:
  - clear-redis 只 `FLUSHDB -n 2` + `-n 3`，**绝不 FLUSHALL**
  - restart-web 用 `systemctl reload httpd`（不断连接）
  - clear-pgsql 严格 `DROP DATABASE quant`（不碰 safebox 库）
  - 不改 pg_hba.conf（适配 safebox 已有 md5 认证）
- **否决的方案**:
  - 独立装 valkey 服务：多一个进程，复用 redis6 更省，协议兼容
  - gunicorn+uvicorn worker：本项目全局状态多 worker 不一致风险
  - 子路径 /quant/：SPA base 路径 + Apache location 配置麻烦，子域名更清晰
  - 时间阈值判断僵尸进程（前棒已否决）：用 Valkey 心跳锁
- **影响**: 部署方案见 `DEPLOY.md`，工具 `scripts/quant-deploy.sh`。safebox 的 FLUSHALL 是遗留隐患（建议 safebox 也改 FLUSHDB -n 0）。


## 2026-08-07 · 类型级同步 trigger 异步化（Celery + progress 轮询）

- **背景**：类型级同步（按日批量拉全市场）是耗时操作（astock_daily 空状态 31.9s，全量重建 37min），原 `trigger_sync_api` 同步阻塞导致 30s axios 超时（数据下完但前端报错）。
- **决定**：类型级 trigger 全部异步化--`trigger_sync_api` 提交 Celery `sync_via_celery` 立即返回 task_id，前端轮询 `/sync/trigger/{sid}/progress`（Valkey `sync:type:{sid}` + AsyncResult 兜底）。与全量重建 `sync_all_symbols`（`sync:progress:{sid}`）统一异步架构，key 分开避免冲突。
- **否决**：① 前端仅加 timeout:120000（最小修复，治标，超大同步仍超时 + uvicorn worker 阻塞期间其他请求排队）；② 空状态走 Celery + 前端兜底（混合，增量 last_sync_date 旧仍可能慢）。
- **保留同步**：per-symbol 单只同步（SymbolManage onSync）保持同步 + 120s timeout（单只快，无需异步）。
- **附带**：恢复 2026-08-04 误删的 6 个 per-symbol 端点（symbols/symbol/backfill/all/progress），靠 engine 函数 + 前端契约重建（force push 覆盖了 git 历史）。

---

## 2026-08-07 · LLM 网关简化：移除 tier 机制 + 移除语言注入（飞书同步）

- **背景**：评审文档 `docs/LLM网关设计.md` 建议移除 tier 与语言注入。核实：**tier 是死代码**--6 个 `gateway.chat` 调用点全 `tier="regular"`，`complex`/`embedding` 从没用过，连最该用 complex 的盘后报告/A股研判都 regular；lang 注入对 i18n 是过度设计（大模型按输入语言自然回复即可，per-机器人 lang 配置增加设置负担）。用户："前期规则不合适可改，做太多设置限制体验不好"。
- **决定**：
  1. **移除 tier**：删 `Tier` 类型 / `llm_model_config.tier` 字段 / `chat()`+`chat_stream()` 的 `tier` 参数 / `_resolve_model(tier)`；`_load_models_from_db` 返回按 `priority` 全局排序的列表；主备容灾取前两个
  2. **移除 lang**：删 `_inject_lang()` / `chat()`+`chat_stream()` 的 `lang` 参数；飞书端同步去 `lang`（`feishu_config.lang` 字段 + `bot.py` 传 lang + 前端设置弹窗 lang 项 + `register_app` 传 `navigator.language`）
  3. **i18n 收窄**：LLM 回复不再"按用户语言偏好"，改为按输入语言自然回复；保留 Web 前端 UI 按浏览器语言切换 + 日志统一英文
- **否决**：补全 tier 使用（盘后报告/A股研判传 `tier="complex"`）--当前就 2 个模型 + 外部 gate 型号待确认，YAGNI；真要 complex 路由等模型多了再加（那时 tier 有实际承载）
- **影响文档**：`architecture/01-llm-gateway.md`（§2/§5/§6/§8/§9）、`CLAUDE.md`（AI 层段 + i18n 段）、记忆 `ai-layer-decision`
- **关联任务**（分析已确认，建议一并实施）：P0.3 工具过滤越权漏洞、P0.4 熔断并发 Lock、gateway Role 对齐 RBAC 四角色（trader 该能 halt）

---

## 2026-08-08 · 平台化通用架构方向（管理设置类菜单通用化 + 6 大接口抽象）

- **背景**：用户明确系统虽自用，但也是开发实践，要架构合理、通用性强、平台化--别人通过配置即可使用（即使数据源/通道/AI/工具不同）。结构接口按通用方向，实现可简化。`docs/管理设置类菜单设计.md` 的通用化理念契合。文档只讲管理设置类，业务菜单不涉及。
- **决定**：
  1. **业务菜单保留**（总览/交易工作台/策略实验室/数据分析/风控业务部分）--文档只讲管理设置类，业务不变
  2. **管理设置类菜单按通用化重组**：系统设置集中配置类（用户与权限/数据服务/AI服务平台/交易通道/系统运维）；命名通用化（数据同步->数据源管理、LLM模型->AI服务平台、飞书机器人->消息通道、账户设置->用户与权限）
  3. **6 大接口抽象**（平台化核心）：DataSource / Broker / MessageChannel / Task / RiskRule / LLMProvider。每个接口=基类 + DB 配置表 + 实现，当前实现一个，别人加实现接口 + DB 配置即接入，不改平台代码
  4. **后台任务管理**（P0 新增核心）：统一 tasks/task_logs 表，所有异步任务（回测/同步/AI/策略）纳入，卡死检测（last_heartbeat）+ 强制删除 + 故障定位（当前 SQL/API/资源）
  5. **跨层联动**：数据限额/AI 预算耗尽 -> 任务失败 -> 后台任务管理高亮 -> 统一告警（MessageChannel 复用）
  6. **AI 用量监控看板**：llm_usage 已写 PG，前端看板（Token/成本/延迟/预算）
- **不接纳**（给理由）：① 具体实现过度设计（WhatsApp/IB/CTP/Prometheus）--接口预留，实现不急；② 菜单完全照搬两大类（保留功能域，配置类归系统设置）；③ AI 场景路由重做（复用 alert_notify 已有分级路由）；④ 系统运维 Prometheus/链路追踪（结构预留 llm_usage/task_logs，不上 Prometheus）
- **影响**：待办加平台化任务项 PT1~PT8；提议 CLAUDE.md 平台架构约束加"平台化通用接口"条（等确认）

---
