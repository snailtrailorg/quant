# 计划 (plan) —— 契约

> 经确认后执行。要偏离，**先改这里**再动手。
> 架构定稿，代码从零开始。

## 里程碑状态

| 里程碑 | 状态 |
|---|---|
| **M1** — 核心基础设施（venv + PG/Valkey + 数据中台 + LLM 网关） | ✅ 已完成 |
| **M2** — 策略框架 + 回测（Strategy/Factor/DSL/BacktestEngine） | ✅ 已完成 |
| **M3** — 加密合约实盘 + 风控 + 告警 | ✅ 已完成（加密合约网关待API开通） |
| **M4** — Web 后台 + 飞书 + RBAC | ✅ 已完成 |
| **M5** — 调度 + 联调 + 部署 | ✅ 已完成 |

## 当前焦点

见 `flow/待办.md`（单一真相源，按优先级排列）。

## 完美系统·挂账全清战役（2026-09-01 用户裁定排期）

> **用户裁定**：系统不仅要实用，也要完美——8 条"接纳但缓做"挂账**全清**，逐批走八步法。
> **部署窗三段**（2026-09-01 用户裁定·完成时长版）：**盘前启动截止 8:55**（竞价 9:15 前让 20 分钟）/ **午休 11:35 起-12:40 启动截止**（12:55 前必完成；全弧一刻钟，启动≠完成）/ **盘后 15:05 起**（收盘让 5 分钟）；彩排绿前提；每批独立观察日；一会话一焦点一天一主批。

| 日期 | 批 | 内容 | 部署窗 |
|---|---|---|---|
| 09-01 二(今) | (已排) | 15:12 三查 → 15:15 批 6b(direct 退役) → 15:20 web 长尾第一批 | 盘外 |
| 09-02 三 | **W1 快赢批** | #5 DSL 因子试算(preview 支持 dsl) + #8 runbookOf 双调用消 + #7 顺手打码 | 当晚盘外 |
| 09-03 四 | **W2 管道冒烟门** | #4 P1-2：探针账号/token 机制 + release postverify 端点断言 + 彩排六场景（W6 制度符合性声明必附） | staging 全验→prod 择机 |
| 09-04 五 | **W3 通知收官批** | #6 外推通道带 code(企微卡片跳转+邮件链接) + #7 全量打码扫荡(剩余点位清零) | 当晚盘外 |
| 09-05 六 | **W4 权限三维化·C** | #1 Permissions C 阶段(nav/api/data × role 矩阵 UI 核心，10 号 §C) | 周末无盘 |
| 09-06 日 | **W5 权限 D+表格** | #1 D 阶段(user 维度/deny 语义/冲突仲裁) + #2b el-table-v2 虚拟滚动(高频表格) | 周末无盘 |
| 09-07 一 | **W6 编辑器+字体** | #2a Monaco DSL 补全(语言定义) + #3 MiSans 分包(cn-font-split 工具链) | 盘外 |
| 09-08 二 | **收官日** | 全站回归(smoke+build+pytest) + 8 项逐条验收 + 文档回写 + 复盘 | — |

**排序逻辑**：快赢先行(#5/#8 半小时级)→管道工程居中(护住后续每批部署)→通知收官(#6+#7 合批清)→大前端组件批留周末(两整天不受交易窗切)→工具链批隔离收尾(#3 引入构建依赖单独验)→回归收官。
**不变约束**：每批八步法全弧；W2 探针账号涉权限制度须 W6 声明；观察日叠加不豁免(各批各自计)。
| **T07** 策略框架：Strategy 基类 + Factor 注册制 + SignalAggregator + DSL 表达式引擎 | Claude Code | 02-策略框架 | `src/strategy_framework/` 模块 | 预置因子注册 → Web 可选 → 配置实例化策略 → on_bar 计算 → 信号输出 |
| **T08** ExecutionAdapter 三实现：AStockReadonlyAdapter(raise) / XTPAdapter / BinancePerpAdapter | Claude Code | 02-策略框架 §5.5 | `src/strategy_framework/adapters/` | A股 adapter 调 send_order 抛 PermissionError；XTP adapter 含 `parse_vt_symbol()` |
| **T09** 可转债双低策略 + Tushare 历史回测 | Claude Code | 04-可转债ETF §5.2，02 §8 | `src/strategies/convertible_doublelow.py` + 回测 notebook | VeighNa 回测引擎加载策略 + 历史数据 → 盈亏曲线 + 交易记录 |
| **T10** 分钟级研判 + 日线选股模型（A股只读，不下单） | Claude Code | 03-A股分析 | `src/strategies/astock_*` | 输出 AnalysisResult 存 PG，adapter 永远 raise |

### M3 加密合约 + 风控 + 告警

| 任务 | 负责角色 | 输入 | 产出 | 验收标准 |
|---|---|---|---|---|
| **T11** 加密永续合约 CTA 策略 + vnpy 网关接入（币安测试网） | Claude Code | 05-加密合约 | `src/strategies/crypto_cta_trend.py` + 适配器配置 | 策略在币安测试网下单/撤单/持仓查询成功 |
| **T12** 风控中心：check_order + emergency_halt + Valkey 无状态熔断 | Claude Code | 07-风控中心 | `src/risk_control/` 模块 | check_order 拒绝超限单；emergency halt 后全策略停开仓；Valkey 直读 |
| **T13** 告警/通知：企业微信/Discord/Server酱 notify() | Claude Code | 10-告警 | `src/alert_notify/` 模块 | 发送消息到各渠道成功，分级路由+配额聚合 |

### M4 Web + 飞书 + RBAC

| 任务 | 负责角色 | 输入 | 产出 | 验收标准 |
|---|---|---|---|---|
| **T14** Web 后端：FastAPI 项目骨架 + 认证(JWT) + RBAC 装饰器 + 审计日志 | Claude Code | 08-Web 后台 §5-§7 | `src/web_api/` | 登录/登出/角色校验/audit_log 写入正常 |
| **T15** Web 前端：Vue3 + Element Plus 项目骨架 + vue-i18n 中/英文 + 登录页 | Claude Code | 08-Web 后台 | `src/web_ui/` | 浏览器语言检测自动切换，手动切换也可 |
| **T16** Web 页面：策略管理（启停/参数改/因子选择+权重+DSL）、A股分析看板、持仓看板、风控面板、日志 | Claude Code | 08-Web 后台 §6，策略配置 schema | `src/web_ui/` 页面组件 | 各页面调后端 API 展示正常，策略配置存 DB → 运行进程热加载 |
| **T17** Web 自然语言查询 + WS 流式 | Claude Code | 08-Web 后台 §5.7 | `/api/chat` + `/ws/chat` | 聊天框输入 → LLM 网关 → 只读工具调用 → 流式返回 |
| **T18** 飞书机器人：Webhook + 鉴权 + 工具调用 + 确认卡片 + 异步回复 | Claude Code | 11-飞书 | `src/feishu_bot/` | 发消息 → LLM 网关 → 读类直执/操作类确认 → 结果回推，3s 超时处理 |

### M5 调度 + 联调 + 部署

| 任务 | 负责角色 | 输入 | 产出 | 验收标准 |
|---|---|---|---|---|
| **T19** 调度层：Celery + beat 定时任务（选股/数据增量/盘后报告）+ 分时休眠 + is_trading_day | Claude Code | 09-调度层 | `src/scheduler/` | 定时任务按 Asia/Shanghai 正确触发，非交易日跳过 |
| **T20** systemd 服务单元（各模块）+ 启动脚本 + 进程保活 | Claude Code | 00-总体设计 §7 | `scripts/systemd/` + `scripts/deploy.sh` | systemctl start/stop/status 各模块正常 |
| **T21** 全链路集成测试：A股分析→数据中台→LLM网关→Web→飞书完整通路 | Claude Code | 全部 | 测试报告 + 验收 | 每个模块的验收标准通过 |

## 实时进展 / 交接棒

→ 见 `flow/进展.md` 顶部（每棒收工在那追加一条：做了什么 / 为什么 / 产出路径 / 下一步）。
（plan.md 只管"计划=契约"；"现在到哪了"在进展日志，不在这儿覆盖。）