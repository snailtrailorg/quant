# 决策日志 (decisions)

> 只记**仍然有效的架构决策**（为什么这样做）。已推翻的已清理；过程考古在 git log。
> 新决策追加在最上面。

---

## 2026-09-03 · SF1 长尾清尾裁定：F-37/38/49 知情接受 + F-50/56 完成

- **F-37/38/49 知情接受（不修）**：F-37 停止延迟已从 direct 60s 缩到 hub 5s（send_order 无 stop_due 门控的残余窗口仅 5s，停止即 `os._exit` 进程整体终止）；F-38 last_price≤0 tick 静默丢是 B4「vnpy 同款」有意设计（停牌标的本就无 bar）；F-49 暖机靠 send_order 时刻 `buy_ok_check`（last_bar_wall<300s）+ hub 流回放间接缓解，`_warmup_history` 不显式校验新鲜度。三者低风险/有意，不再扩门。
- **F-50/F-56 本批完成（独立复核后不推迟）**：F-50 核心=order_log 加 vt_orderid 列（迁移 0063）+ write_trade_log 重启后 vt_orderid 反查（消除 order_id NULL 误判）+ 「委托不成交」口径收紧排除 send_failed；撤单 canceled 终态涉及 EVENT_ORDER 监听分层，不做（口径收紧用 status='submitted' 已隐含排除）。F-56 子问题 1=worker TD 独立 client_id（runner_client_id 派生 2-99，XTP 普通用户 1-99 见 xtp_trader_api.h:602）；**子问题 2（SDK 目录）判定不修**——MD 写 quote.log、TD 写 trade.log，文件名天然区分，目录级共享非并发冲突（子代理的「并发互写」系未复核的过度判断）。

## 2026-09-03 · 冒烟门抓修 3 真 bug + 令牌/密码/staging 若干裁定

- **canvas-var 走 cssVar 解析器**（用户裁定①）：批一 A 档把 echarts 图内色换 `var(--)`，但 canvas 不解析 var()（zrender 直传 fillStyle 静默丢弃）。裁定加 `utils/cssVar.js`（getComputedStyle 解析）替换全站 21 处，而非回退字面 hex——保住令牌单一源 + 暗色自适应。
- **staging fixture 撤销 mock 分支**（用户澄清）：本地「量化交易助手」（dev DB id=2）真实可用、走 webhook 路径；`quant-feishu-bot@2` 长连接单元重启 dwell 不稳。撤销 ws_client mock 分支 + 假 bot 种子，staging 波次回退 web/celery/hub 三波次（feishu 长连接凭证待诊断）。
- **令牌「不扩门 + 语义就近」**（用户裁定）：#999/#303133/#f8f9fb 等 EP 默认残留换语义就近令牌（接受变色）；令牌门**不扩** layouts；「绿=跌」只限国内蜡烛图，DataIntegrity complete `#67c23a`→`--success`。
- **生产密码=临时测试密码**（用户澄清）：`tianran3B` 是为测试设的临时密码；环境变量化（SMOKE_PASS）后 repo 明文清零，无需改密码。

## 2026-09-02 · 告警订阅分发架构：三通道 Celery 队列化+notifications.dispatch 全程审计（用户三轮裁定+双盲审三轮）

- **订阅模型**：全局一套（alert_channel_sub 三行 im/email/sms），每通道独立类别多选+min_level 门槛（warn+ 可调）——取代旧 critical→discord 硬编码路由（channel_config webhook 链保留为过渡兜底，零订阅时回落，订阅配好自然失效）
- **异步铁律（用户裁定）**：推送必须队列化不阻塞业务——notify() 同步增量=一次 queue.put；发送全在 risk worker（alerts_im/email/sms 三队列，-c 1 与长任务隔离）或降级 daemon 线程
- **全程可审计（用户裁定）**：notifications.dispatch jsonb 回写——ok/queued/sending/failed:token/skip:token；{}=零外推终态,null=未跑完（死亡窗）——第四种"说不清"不存在；网页通知页 chips 可见
- **双发窗封死**：claim 认领式（queued→sending 单向迁移,rowcount=1 才发）——降级直发与 worker 只有一方获得发送权（短信计费敏感）
- **凭证面**：SMS 走 system_config 加密列（smtp 先例,Web 配零重启到位即通）；alerts_config 权限 admin 专属锁三处（analyst 有 system_config 不能触告警路由/计费面）
- **分层层级**：celery 任务定义归 scheduler(3)；alert_notify(2)→im_bot(3) 走 EXEMPT_UPWARD 成文豁免
- 详 docs/任务/批7-告警订阅分发.md（三轮双盲审 49 条全吸收的完整契约）

## 2026-08-19 · 分钟数据源策略：XTP 自攒为主，Tusharestk_mins 产品包后启（用户拍板）

- **决定**：Tushare stk_mins 是独立产品包（2000 元/年），当前全局 1 次/小时不可用。池驱动分钟同步基础设施已建（`data_sync/pool_minute.py` + beat + 限速闸门 + API 端点）但** beat 禁用**——买包后取消注释 beat + `data_source_config.params` 配 `rate_limits` 即启用。
- **分钟数据路径**：XTP hub 订阅池标的自攒（影子期后写 bar_1min 正表），Tushare 将来作为主源、XTP 辅助校验。
- **复权因子**：已回填 95.4%（API 限速宽松），剩余 5% 部署窗口后续填即可。
- **关键实测**：stk_mins **全局** 1 次/小时（非 per-symbol）→ 30 只池日度增量需 30 小时，不可行。

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

## 2026-08-22 · SECRET_KEY 根密钥方案（用户拍板）

- **问题**：需两个独立密钥 JWT_SECRET + ENCRYPTION_KEY，漏设一个就告警，JWT 轮换会孤儿化加密凭证
- **决策**：一个根密钥 `SECRET_KEY`，HKDF-SHA256 派生子密钥（`info=b"jwt"` → JWT 签名，`info=b"encrypt"` → Fernet 加密）
- **优先级**：SECRET_KEY（推荐，无告警）→ ENCRYPTION_KEY 单独设置（向后兼容）→ JWT_SECRET 单独设置 → JWT_SECRET sha256 派生（旧行为，告警）→ 进程内随机密钥（重启孤儿化，critical）
- **技术选型**：HKDF（RFC 5869，已含在 cryptography 库中），salt=None + info 域分离
- **迁移**：脚本更新为从 SECRET_KEY 派生新密钥，旧密钥仍从 JWT_SECRET 派生
- **向后兼容**：JWT_SECRET / ENCRYPTION_KEY 环境变量仍可单独设置，SECRET_KEY 未设时行为不变

## 2026-08-24：运行时韧性分层模型 + market_session 配置化

**背景**：开盘三验证暴露两大故障（hub 僵尸会话 + 任务 8 停机 2.5 天），暴露出系统缺乏统一的故障处理框架。

**决策**：
1. **运行时韧性分层模型**（L1 机器层/systemd / L2 会话层/进程内自愈 / L3 意图层/调和器）：
   - 退出/重启只属于进程域故障（崩溃/挂死/配置错）。数据流症状永不杀死进程。
   - 外部世界故障 = 进程内无限重试 + 有界退避 + 告警，无退出路径。
   - 已知周期失效用定时续航，未知失效用反应式重登。
2. **MdSession 契约**：行情会话生命周期抽象，引擎只依赖契约，平台领域知识全封子类。
3. **market_session 配置化**：交易时段从硬编码改为 DB 配置驱动，`set_config_provider` 回调避层层 0→1 依赖。
4. **SA4 重新定位**：只服务「进程真的死了」，不接数据流症状的 exit。
5. **告警通道未配**（行动项）：请 Web 消息通道页配 channel_config。

**替代方案**：零 tick 退出让 systemd 重启（跨层滥用，弃用）

## 2026-08-25 · L2 不加"升级退出"条款（用户裁定）

- **背景**：架构对标（20 号文档）发现 §2.8 两硬规则存在死角——进程活着但 SDK 状态毒化时（08-25 晨 3h 僵尸形态），L2 原地自愈久攻不下，规则一禁止退出，最终靠人 restart。曾提案 OTP intensity 式精化（L2 连续失败超阈值→升级 L1 一次）。
- **裁定**：**不加**。两硬规则保持绝对；活毒状态接受为已知残余风险，处置=告警+人工。理由：防重启风暴的确定性优先于罕见场景的自动恢复。
- **含义**：后续会话/批次不得再提议 L2→L1 升级退出路径。

## 2026-08-28 · 批 6 跳过 ST7 影子门禁、阶段 1 先切换（用户裁定）

- **背景**：原计划 ST7 门禁 ≥4/5 干净日→批 6 阶段 1。观察日三查揭门禁假绿+双轨差异四分类；tcpdump 包级实证仿真平台对两条 MD 连接推送不一致（1616B 快照包 373 vs 119，方向翻转）——零差异门禁在仿真环境结构性不可达，容差口径决策悬而未决。
- **裁定**：**先切换，周一生产验证**（hub 模式运行验证代替影子门禁）。依据：①hub 输入质量实证优于 worker-MD（25 vs 8 快照/分）——切换=信号源数据质量提升；②15:01 竞价分钟两侧逐位一致证明等价性可达；③门禁口径决策成本>收益，切换后该决策消解。
- **配套**：任务级+全局 md_mode 双切（盲审 B-P1-1 防混态）；配置级秒级回滚（改回 direct+restart）；direct 代码保留至 6b 验证绿后退役；周一验证清单（首根消费/TD 登录确认/gen_jump 误导告警勿动作/metrics 标签）。
- **同期裁定**：三查两站制（09:31 查昨日全量+15:10 查今日全量，门禁以盘后为准）+ST7 计数作废重置。
