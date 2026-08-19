# 决策日志 (decisions)

> 只记**仍然有效的架构决策**（为什么这样做）。已推翻的已清理；过程考古在 git log。
> 新决策追加在最上面。

---

## 2026-08-19 · 链条打磨：因子→实盘全链打通（四批，26 断点清零）

- **起因**：用户要求链条"打磨成熟、功能完善、体验良好+操作指导书"。探查实测 26 断点——4 整段断裂（自定义因子出不了 API 进程/因子模式实盘零下单/回测实盘频率+参数双错配/预检失败当成功）+2 UI 页失效+DSL 死功能。
- **关键决策**：①执行规则**方向感知**（R-F1：SELL=持仓口径 ALL_IN 清仓/BUY=可用资金——持仓走 ST2 position_snapshot 真相源）②DSL 实现而非删除（8 窗口函数+静默错值四形态抛异常——R-F2：错值比崩溃危险）③旧启停移除（LiveTask 唯一入口）④指导书分册+Web /help 内置都要⑤因子试算=写完即看曲线（真实 bar 喂 compute）
- **架构联动**：自定义因子三进程加载（web/celery/runner）+回测任务头 lazy 重载+factor:recalc 兼热重载钩子；全败 run 不过 F-44 验证门
- **指导书**：docs/操作指导 五册随 rsync 部署（server/docs/ 镜像），/api/help/{topic} + Web /help（marked 渲染）

## 2026-08-19 · 模块归位：quant_common 底座 + 分层断言测试（消 6 条层级违规）

- **起因**：用户质询"多轮修改后高内聚低耦合还成立吗"——依赖图实测 6 条层级违规，根因全是"共享工具/逻辑寄生错误位置"（crypto 在 web_api/时段工具在 runner/预算检查在 HTTP 入口/审计在 auth），非发散性腐化。
- **决定**：建 `quant_common`（层 0，crypto 纯函数/session/guard 回调注入/terms 注册表，白名单 cryptography+dotenv）；audit_log→data_platform；build_xtp_setting→strategy_framework/broker；check_budget_alerts→llm_gateway（随迁 notify 化——预算预警从直推企微改进站内铃铛）；email_service 独立模块。原址留 re-export 保兼容。
- **守门**：`test_layering.py` 4 断言（quant_common 纯度/层级禁上行含 lazy/历史违规边回归锁/第三方白名单）——分层从理念变测试，违规即 CI 红。
- **豁免登记**（唯一双向环）：data_platform↔alert_notify（tushare 同步告警，横向服务，回调注入为未来优化项）。
- **教训**：s.replace 无 assert=静默 no-op（hub_worker"直连"宣称不实被 Q 实锤）；py_compile+全绿测试都抓不到顶层双定义/静默未中的搬移——**assert 是搬移手术的必备缝合线**。

## 2026-08-18 · ST2 持仓真相源 + PUT→POST + schema 生成式基线（三联决策）

- **ST2（消 D2）**：持仓真相=券商 query_position 快照（`position_snapshot` 当前状态表：每批同事务 DELETE+INSERT，空批可表示空仓）+ `position_refresh` 心跳（stale≠空仓）。写入点=60s 循环取返回值（**否决** EVENT_POSITION handler——direct 模式 vnpy init_query 每 4s 常推会散批+15 倍量级）；TD 断线守卫内不写假空仓。trade_log 推导降级为 /api/reconcile 第四比对（归因）。生产实证 direction=Net——端点不过滤 direction。
- **PUT→POST 硬切（A 案）**：对齐业界趋势（Google AIP-136 等避免 PUT）。16 端点+前端 17+契约 19 处；**路由遮蔽教训**（参数化 POST 路由会吃后注册的静态路由）→ 4 静态路由调序 + 结构化顺序断言（test 锁全路由注册序）。结构化顺序断言模式可复用于任何路由变更。
- **#48 schema 生成式基线**：期望清单=迁移链 scratch 产物（schema_expectations.txt，**禁手写**——手写清单必腐有仓内实锤）；verify_schema=纯函数单向存在性（expected⊆actual），告警路由归入口层；四入口接线（web/runner/hub/celery 父进程）。**每加迁移必须重跑生成命令**（db.py load_schema_expectations docstring）并提交。
- 八段工作线（方案→审核→代码→审核→本地测试→部署→生产测试→提交）同日定型为默认流程。

## 2026-08-18 · S6 修订：断流不自杀 + 安全判定挪下单时刻 + 双层监控（内部 health_monitor / 外部 Zabbix@NAS）

- **决定**：①hub/direct 的 tick 断流自杀（300s os._exit）删除，只告警（文案带 runbook）；staleness 基线一律**时段作用域**（进入沿清零）。②BUY 安全判定从后台定时器预计算的 `frozen["now"]` 改为 **send_order 时刻事实检查**（`buy_ok_check`：bar<300s+hub 心跳），日历/交易所规则从动作路径清零；sticky 冻结（untrusted/gap 污染事实）保留。③监控双层：内部 `src/health_monitor/`（30s beat，症状型规则+沿检测+health_event 落库+自身心跳供外部反监）；外部 Zabbix server 装 **NAS**（常在线+自带通知），agent 装 quant 服务器，标准模板+systemd 插件+`/metrics` Prometheus 格式拉取。
- **为什么**：hub 每晨 09:31 必自杀（基线跨日污染，34627s 假断流）实证了"把交易所/平台节奏预期编进守卫触发器"必翻车；重启治不了平台/网络问题（只治进程自身），真僵尸态罕见且可观察，误重启每天发生——交换正确。暴露端对齐业界（/healthz /readyz /metrics=Prometheus 文本），不自造格式。
- **边界**：真"连接正常但数据不流"僵尸态改为响亮告警+人工重启（runbook）；后续可按数据加"长时间才重启"末档。
- **详见**：`docs/architecture/15-服务监控设计.md`（设计+职责划分+runbook）；12 号 ST4 节已加修订指针。

## 2026-08-15 · N 语言架构约束：注册表驱动 + en 缺省

- **决定**：多语言设计为支持任意语言（当前实现 zh/en），英语为不匹配时缺省。所有语言相关逻辑改为**注册表驱动**（dict/array），不写死双语。
- **加新语言=只加条目零逻辑改动**：locales/index.js + i18n.js LANGUAGES + terms.py TERMS/LANG_NAMES + 邮件模板 dict。
- **各层策略**：页面=浏览器语言自动；条款=全语言纵向堆叠（不依赖检测）；邮件=操作者界面语言（请求传 lang）；LLM=跟随输入。
- **禁令**：写死 zh/en 二元判断、独立语言变量（TERMS_ZH）、硬编码语言列表。测试锁约束（test_new_language_only_needs_entry）。

## 2026-08-15 · 后端错误码化：字符串码 + 前端本地化映射

- **决定**：`ApiError(status, CODE, 中文兜省)` 响应 `{detail, code}`；前端 `apiErr(e)` 优先 `err.<CODE>` 本地化、无映射回落 detail。
- **否决数字码**（如 40001）：不自描述、要查表。
- 用户流程 22 处已迁移；深层管理接口增量补码（未映射自动回落安全）。

## 2026-08-15 · 末位 admin 保护：管理页移除 / 自助注销保留

- **管理页**：不可达（user_mgmt=admin-only + 不能动自己 ⇒ 操作者若是另一 admin 则目标非末位），删除死规则。
- **自助注销**：真实可达（用户对自己操作，不经管理页），`guard_self_deactivate` 单独设防（唯一启用 admin 不可注销自己）。
- **教训**：规则设计时验证可达性，不可达的规则是死代码。

## 2026-08-15 · 参考方案借鉴四批次（用户管理）

- **背景**：用户提供的邀请制用户管理参考方案，对比后 3 处原则冲突不借鉴（邀请预设 Trader / 超管分层 / 数字错误码），6 项借鉴分四批落地。
- **A**：禁用提示 / 邮箱登录 / last_login / JWT jti 黑名单
- **B**：邀请记录列表+撤销
- **C**：昵称+头像+Profile 页+顶栏改造
- **D**：软删除+脱敏+自助注销

## 2026-08-14 · SMTP 配置 DB 化（弃 .env）

- **决定**：SMTP 五项走 `system_config`（前端系统配置页可改），`.env` 不再参与。`SMTP_DEV=true` 仅本地显式开发模式。
- **理由**：单一真相源；.env 难改且易残留旧值（BASE_URL IP 问题的教训）。

## 2026-08-14 · 通知中心：类别×角色可见 + 仅实盘紧急外推

- **三项用户决策**：① 可见范围按类别×角色（email→admin 等）；② 外部通道只推 risk+critical（实盘紧急），订阅型 report 保留；③ 告警历史从 Valkey 直接切 PG（旧数据不迁移）。
- **行为变化**：磁盘/接口健康 critical 不再外推（仅站内）。

## 2026-08-13 · DDL 全部入迁移 + 运行时 DDL 清零

- alembic 迁移为唯一真相源；`verify_schema()` 启动校验；30 处运行时 CREATE TABLE 全删。

## 2026-08-10 · Codex 失效，Claude 直接做

- subcodex 复杂任务返 Ark 400（function calling bug），当前 Claude 直接编码，full-subagent 铁律豁免。

## 2026-08-09 · 自包含任务文档体系

- 新待办按 `docs/任务/<id>.md` 写（8 字段），做任务只读任务文件+接口契约+模块契约，零代码阅读。

## 2026-08-08 · 平台化通用架构方向

- 6 大接口抽象（DataSource/Broker/MessageChannel/Task/RiskRule/LLMProvider），别人配置+实现接口子类即可接入，不改平台代码。

## 2026-08-07 · LLM 网关简化

- 移除 tier 机制（按 priority 全局排序）+ 移除语言注入（LLM 按输入语言自然回复）。

## 2026-08-03 · 策略+回测体系架构

- 四层（Strategy/Factor/SignalAggregator/Adapter）+ ActionSignal 契约 + 因子注册制 + 风控覆写 + DSL/Python 双模式统一执行。

## 2026-08-03 · 策略实盘化：修正版 B

- 每策略独立子进程（systemd quant-strategy@<id>）+ 独立 vnpy MainEngine + XtpGateway 实时驱动（tick→BarGenerator→on_bar）。

## 2026-08-03 · A 股进实盘开关（废止只读）

- AStockReadonlyAdapter 废止，A 股统一走 XTPAdapter；实盘三级开关 AND：.env 总闸 + Web 分项 + 策略级。

## 2026-08-17 · 实盘链路验证收尾（新架构定语义 + 运维硬化）

- **live_task 是新架构唯一运行语义**：runner 停止条件查 `live_task.status`（stop_live_task 置 stopped）；`strategy_config.enabled/backtest_verified` 只作创建/启动时的门禁。双 unit 归位：`quant-strategy@`（--id 旧架构）/ `quant-live-task@`（--task-id 新架构），polkit 两者都放行。
- **服务器加 2G swap**（1.8G 内存跑不动 runner 的 XTP 合约加载尖峰，全局 OOM 实锤）；runner MemoryMax=1G。
- **部署实例清单真相源 = DB**（feishu_config），不再"收集 active 实例"（会把误启的幽灵转正）；install-services 以 root 跑 + restart polkit + 输出 unit 状态快照。
- **当天验证结果**：tick→bar→on_bar→signal→risk→XTP order 全链通（证据在进展.md §0）；trade_log 写入缺失转 #46。

## 2026-08-17 · XTP 改共享行情进程架构（用户拍板）+ 稳定性检查方法论先行

- **架构决定**：XTP 侧改"共享行情 hub 进程（持有 XTP 连接+合约表）+ N 个轻策略 worker"，用实时性换内存（国内市场 tick 密度低，分钟 bar 足够）。与"修正版 B 每策略独立进程"的隔离性权衡：进程隔离弱化为"hub 单点 + worker 独立"，hub 稳定性要求因此**更高**。设计前必须先做稳定性需求书。
- **流程决定**：动手检查/改架构前，先审定 `flow/规范/稳定性检查方法论.md`（五轴枚举矩阵 + 四层检查手段 + 双盲交叉验收），检查按方法论执行，防经验式清单遗漏。crypto 侧维持独立进程（无内存痛点，纯 API 轻量）。
