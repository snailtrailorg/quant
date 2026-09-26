# 决策日志 (decisions)

> 只记**仍然有效的架构决策**（为什么这样做）。已推翻的已清理；过程考古在 git log。
> 新决策追加在最上面。

---

## 2026-09-26 · A04 §九实施语义 Web 域诚实化（批 61 M6）

1. **两条立法降级实施**（立法意图保留，A04 §九注记为实施真相）：①「提名时刻连通预检」Web 域不可达（`Broker.test_connection`=纯凭证检查零网络；Web 进程无 TD 会话）→落地 **L1=凭证完整性+行校验**，L2 真连+资金挂账 worker/hub 会话探测批；②「在途清零」两源（order_log 无终态回写——成交/撤单不落 status）→DB 近似+确认者人工核实勾选，order_log 终态回写根治挂账。
2. **确认面=Web 五闸等价**（29b 卡绑飞书 ws 不可直连）：时效=DB 时钟 SQL 谓词/身份=JWT/权限=require_perm（execute=trade 键 analyst 不可达）/dedup=状态机+FOR UPDATE/exec=单事务。
3. **TradeBus 四方法=worker 链继承**：place/cancel/query 已在 worker 进程落地（时序纪律/幂等全现成）；Web 手动下单永不做（XTPAdapter 无 gateway 返 mock- 前缀=实盘事故面）；幂等键不迁移（`t{tid}:e{boot}:c{seq}` 前缀结构性唯一强于复合键）。

## 2026-09-26 · TD provider 插件注册表立法（批 65 D26-A/批 63 P4 实证）

1. **TD builder 必须注册于 `strategy_runner/td_registry.py` 模块内**（import 即注册；独立文件注册=空表静默）——单文件自足约定。
2. **键域守门锚 `PROVIDER_MARKET` 非 `Broker._REGISTRY`**（后者无 emt_emq=前向地雷；两角色非 1:1——crypto 有 Broker 无 TD builder）。
3. **EmtAdapter vnpy 形状合成为强制契约**：worker 四消费链（reconcile/halt_edge_cancel/write_trade_log/snapshot_cycle）假定 vnpy OrderData/TradeData/AccountData+事件引擎——任何新 TD adapter 必须在 SPI 回调内合成 vnpy 形状经 ee 推流，**查询分帧必须帧内物化**（SDK 指针仅帧内有效）。

## 2026-09-25 · 全市场统一账号级 hub 拓扑（推翻 A05「A股 MD 单 hub」部分裁定）

1. **终裁**：全市场唯一拓扑=**账号级实例化**——一行 trading 域接口配置=一个账号=一个 hub+一套 TD 会话；hub 绑死账号行（MD/TD 同凭证，裁定 A：不做 md_row 跨通道解耦）。XTP 双账号=双行=双 hub（空间换简洁）。
2. **推翻 A05 五理由**：①分支税（A 股/加密 10+ 处分支，D6 键分叉/flush_stale 等历史 bug 本质是分支税）；②故障域反转（单 hub=全平台单点故障；per-account=故障域恰为账号域——A05「N-1 多余故障点」视角是反的）；③一致性反对弱化（两份聚合 bar 微差在 A03 双时态语义下非新罪：流生成版本本就允许微差，权威=盘后 PG 回补）；④M5 退役红利（互备体系存在的全部理由=多实例抢一键空间，统一后无对象，switch.py/active_instance/intent/guarded Lua/AB 双实例整体蒸发；gen 保留，lease 简化）；⑤**底座同质化**（最根本：「聚合 hub」特殊形态组件消失——hub 从共享基础设施退化为账号附属进程；若保留共享单例，hub 底座须双模式注册，解耦复杂度落在底座上传染全系统；统一后单一形态=注册即行 CRUD、代码路径唯一、故障模式单一）。
3. **连带退役**：HUB_INTERFACE_ROW 概念（hub 选行语义消失）；position/拖拽的通道切换语义（reorder 端点退役；tushare 多数据源行优先序与 routing 软排序保留）；SA4 期望态统一按行（A 股特例消失）。
4. **不变式修订**：「同源数据不复制」全市场统一为 **best-effort**（对账锚=PG 盘后回补；消费方不得假设跨账号同 bar）。
5. **哲学（用户立法）**：逻辑越简洁代码质量越高，真实故障点越少；资源不足加配置即可。
6. **载体**：`docs/design/D26-市场接入架构.md` v3（§零推翻记录）；**实施批方案细化须重新双盲审**。

## 2026-09-24 · 术语正名：venue 概念废除（账号→account、交易所→exchange）

1. **venue 一词彻底废除，按语义拆两词**：D1-D6 的 venue（=交易账号/external_interface 行）→ `account`（对齐 vnpy `AccountData` / QuantConnect `account` / FIX Tag 1 `Account`——行业里 venue 本义=交易场所/交易所，拿来指账号是语义错位）；`MARKET_OP_DECOMP` 第三元（=BINANCE/OKX 交易所）→ `exchange`（归一到已有 `EXCHANGES` 概念，本就该叫 exchange）。
2. **迁移策略**：新增 0104 rename（6 表 `account_id` 列 + `account_permission` 表 + 18 约束 rename + 对账数据 UPDATE）；历史迁移 0097-0103 不改（alembic checksum 不可变，squash 需回滚破坏性迁移有损）。交易所语义 venue 仅存在于注释/docstring（代码标识符早已用 exchange），归位零 DDL。

## 2026-09-23 · 多账号源架构重设计（30 号废弃 → 31 号框架 + D1-D6 详细设计）

1. **重设计方法**（用户裁定）：30 号 v1-v15 共 15 轮 4 盲审未收敛 → 全部重来、换 session。方法=**先写简要但明确的整体框架方案（定义架构+契约）→ 再分头写几个小详细设计方案**。30 号标「废弃」仅参考，不在其上改。
2. **评审角色换「量化交易高手」替「金融专家」**（方法论裁定）：金融专家会求全金融合规边角（与个人平台定位冲突），量化高手只审「真正影响交易」；框架双盲审=软件架构专家+量化交易高手两角色。
3. **Account = 交易账号**（external_interface 行），非「券商」；同源分市场：A股=MD 单 hub（L1 全市场同源，doc14 M5 保留）+ TD per-account；加密=MD+TD per-account。同源校验=跨进程运行时事实（流带 account_id，worker 比对），非「同一行派生两值」空转断言。
4. **权限三维正交 + 数据字段化四首决**：board **新增枚举列**（main/star/chinext/bse，从 asset_static_info.market 中文归一化回填，否决复用 market 列——中文文本当键=同类漂移）；account 权限存 **新表 account_permission**（否决 external_interface 加列/扩 live_trading_config）；is_st 官方名单 fail-closed；强赎/退市整理期是价格事件需字段（非边角排除）。
5. **身份与数据隔离三首决**：稳定语义键=**资金账号**（external_interface.account_key UNIQUE，股东账号沪/深各一不适合单列键）；**leverage→account 级**（账号级，账户级敞口实参，券商/交易所约束）；资金基线写侧 per-account（initial_capital 不再取策略级默认）。



1. **历史分钟数据「不自攒、买正规」**（用户裁定）：自攒 A 股分钟数据质量存疑（腾讯攒竞价条错位 / 320 根滚动窗口漏一天断 ~4h；XTP bar_hub 自攒与实时分发耦合），维护成本高。退役自攒链路（断档接受），将来买 Tushare `stk_mins`（2000 积分/年）正式数据接入。设计真源 21 号本就是「腾讯攒过渡 + Tushare 终极」，本次提前结束过渡期。
2. **边界 = 删「攒」留「读」**：删腾讯攒 + XTP bar_hub 落库 + minute_symbols 管理面；保留实时行情分发（`MinuteAggregator` + XADD 流——实盘下单输入，行业标准打法）+ stk_mins 接入位（`engine.py` 分钟编排 + `TushareAdapter`，将来买积分即用）+ 日线。`bar_1min`/`bar_5min` 表结构保留（将来 stk_mins 填），只清空腾讯存量。
3. **分钟数据源切换语义简化**：`minute_data_source` 互斥开关随腾讯攒删除——将来 stk_mins 接入不需「互斥切换」，直接启用 `engine.py` 的 stk_mins 路径（21 号 §3.4 切换逻辑随之简化）。
4. **断档影响接受**：因子试算 1min/5min 档硬失败（返回「无数据」）、实盘暖机 history 空（流回放 240 根兜底、double_low 静态因子不受影响）、Web 覆盖状态/完整性看板 1min/5min 显示空——均无交易/回测硬断。

## 2026-09-23 · 批 63 部署三裁定 + 批 64 Web 切换裁定

1. **EMQ 编译链用 gcc-toolset-13（不 patch vendor 头）**：EMQ 头 `quote_api.h` 用 `EMQ_EXCHANGE_TYPE::EMQ_EXCHANGE_UNKNOWN`（C++23 P1099 enum 作用域限定），服务器 GCC 10.2 编译报错。用户裁定装 gcc-toolset-13（`source /opt/rh/gcc-toolset-13/enable`），不改东财 SDK 头（保留上游原样，升级 SDK 时不丢 patch）。
2. **切换 provider 用「自动重启」而非「热切换」**（批 64）：hub 对账 `config_version` 检测 provider 变化 → `os._exit(9)` 自重启读新行。不进程内 close 旧 gateway + 建新——违背项目铁律「原生库拆除规避」（hub 退出走 os._exit 带码自灭）。代价 ~30s 停机窗（盘外零损失）。
3. **外部接口切换纯 Web 操作**（批 64）：集成中心拖拽改 position + bump config_version 即触发，不需 drop-in/root。`HUB_INTERFACE_ROW` 是 M5 双实例（A/B 账号）机制，非 provider 切换场景。

## 2026-09-22 · 批 60 M5 流协议五裁定（方案集 v16 定稿）

- **M5 切换协议 = Valkey 四键 + guarded Lua + 无待命态**（推翻 28/29 蓝图的 stream_registry/影子预热/fencing token）：`hub:gen/lease/switch:intent{snapshot,target}/active_instance` 四键承载全部状态；B=A 让位后按需启动的正常 hub（非提前待命——main.py 单遍初始化决定「接管≈全量重启」，待命省不了连接时间）；「计划切换零丢包」降级为「切换仅限非交易时段 + 盘后回补兜底 + `switch.py diff` 口径比对」并已版本化注记进 28 §8.2/29 §八。
- **guarded Lua 三态原子是切换核心**：首接（gen==snapshot→INCR+抢 lease+SET active_instance）/重启（gen==snapshot+1→只抢 lease）/污染（→拒，校验在 INCR 前零污染），正切 B/反切 A 通用；`active_instance==target` 是「目标已接管」的等强度代理（三等价：active_instance==target ⟺ guarded 成功 ⟺ 持 lease），由此砍掉 HUB_UUID 预定注入（无特权通道回写 drop-in）。
- **boot 单判定 + intent 记 target**：切换方向建模在 intent（正切 target=quant2/反切 target=quant），boot 闸据此放行目标/拦截被切走者（exit 6 Prevent）；active_instance 只兜「切换完成后服务器重启」仲裁，normal 冷启也 SET（首启即生效）。
- **退出码矩阵 Prevent=0 6 78，3=真让位回归重启码**（staging 实证）：SIGTERM 不 DEL lease，正常重启有 30s lease 滞后窗，靠 exit(3)→on-failure 30s 重拉自愈；切换窗复活拦截由 boot 闸 exit(6) 独立承接——一码多路径，Prevent 决策逐路径判定。
- **评审方法论**（用户原则沉淀）：方案打磨期「换强模型对抗式单轮」优于同模型多轮（两轮 opus 深审独立命中同一 P0）；**自己先往死里推演主场景（正切/反切/重启/崩溃）再交评审**——v11 boot 双闸正反切矛盾即自推演发现，评审抓「设计对不对」、自推演抓「实现会不会卡」；盲审不收敛=前期工作不够，但「方案已成熟+审核通过」也要敢进编码，不能无限打磨。

---

## 2026-09-13 · 表格交互与个人中心四批裁定

- **列宽拖拽全站+持久化（批17）**：EP v1 原生拖拽+TableShell 包装补 localStorage（colw.*）+双击回声明宽；v2 三表自实现手柄（v2colwidth）。业界调研（AG Grid/vxe-table/TanStack）裁定不引库——能力缺口 ~300 行自建补齐，迁移 2-4 周零收益。
- **列显隐=例外解非通用解**：仅「列数溢出且各列各有受众」的表配 ColumnSettings（现 18 张）；列少的表配了=空壳噪音。
- **列宽/显隐 localStorage 治理**：键名锚定语义 prop **永不再变规则**；禁自动清理/迁移代码；清档=给键名用户手动清（详记忆 colw-localstorage-policy）。
- **邮箱修改=验证成功才改**：不建 email_verified 列、不存 pending 新邮箱——确认前库中零痕迹，失败即无事发生（用户裁定）。
- **出站邮件封面名「人工智能开发学习平台」沿用**：既有低调封面设计，改邮箱/找回密码/邀请三模板统一，非站名「蜗牛量化」。

## 2026-09-03 · SF1 长尾清尾裁定：F-37/38/49 知情接受 + F-50/56 完成

- **F-37/38/49 知情接受（不修）**：F-37 停止延迟已从 direct 60s 缩到 hub 5s（send_order 无 stop_due 门控的残余窗口仅 5s，停止即 `os._exit` 进程整体终止）；F-38 last_price≤0 tick 静默丢是 B4「vnpy 同款」有意设计（停牌标的本就无 bar）；F-49 暖机靠 send_order 时刻 `buy_ok_check`（last_bar_wall<300s）+ hub 流回放间接缓解，`_warmup_history` 不显式校验新鲜度。三者低风险/有意，不再扩门。
- **F-50/F-56 本批完成（独立复核后不推迟）**：F-50 核心=order_log 加 vt_orderid 列（迁移 0063）+ write_trade_log 重启后 vt_orderid 反查（消除 order_id NULL 误判）+ 「委托不成交」口径收紧排除 send_failed；撤单 canceled 终态涉及 EVENT_ORDER 监听分层，不做（口径收紧用 status='submitted' 已隐含排除）。F-56 子问题 1=worker TD 独立 client_id（runner_client_id 派生 2-99，XTP 普通用户 1-99 见 xtp_trader_api.h:602）；**子问题 2（SDK 目录）判定不修**——MD 写 quote.log、TD 写 trade.log，文件名天然区分，目录级共享非并发冲突（子代理的「并发互写」系未复核的过度判断）。

## 2026-09-03 · 冒烟门抓修 3 真 bug + 令牌/密码/staging 若干裁定

- **canvas-var 走 cssVar 解析器**（用户裁定①）：批一 A 档把 echarts 图内色换 `var(--)`，但 canvas 不解析 var()（zrender 直传 fillStyle 静默丢弃）。裁定加 `utils/cssVar.js`（getComputedStyle 解析）替换全站 21 处，而非回退字面 hex——保住令牌单一源 + 暗色自适应。
- **staging fixture 撤销 mock 分支**（用户澄清）：本地「量化交易助手」（dev DB id=2）真实可用、走 webhook 路径；`quant-feishu-bot@2` 长连接单元重启 dwell 不稳。撤销 ws_client mock 分支 + 假 bot 种子，staging 波次回退 web/celery/hub 三波次（feishu 长连接凭证待诊断）。
- **令牌「不扩门 + 语义就近」**（用户裁定）：#999/#303133/#f8f9fb 等 EP 默认残留换语义就近令牌（接受变色）；令牌门**不扩** layouts；「绿=跌」只限国内蜡烛图，DataIntegrity complete `#67c23a`→`--success`。
- **生产密码=临时测试密码**（用户澄清）：生产 admin 密码是为测试设的临时密码（明文见 server-info 记忆，不落 repo）；环境变量化（SMOKE_PASS）后 repo 明文清零，无需改密码。

## 2026-09-02 · 告警订阅分发架构：三通道 Celery 队列化+notifications.dispatch 全程审计（用户三轮裁定+双盲审三轮）

- **订阅模型**：全局一套（alert_channel_sub 三行 im/email/sms），每通道独立类别多选+min_level 门槛（warn+ 可调）——取代旧 critical→discord 硬编码路由（channel_config webhook 链保留为过渡兜底，零订阅时回落，订阅配好自然失效）
- **异步铁律（用户裁定）**：推送必须队列化不阻塞业务——notify() 同步增量=一次 queue.put；发送全在 risk worker（alerts_im/email/sms 三队列，-c 1 与长任务隔离）或降级 daemon 线程
- **全程可审计（用户裁定）**：notifications.dispatch jsonb 回写——ok/queued/sending/failed:token/skip:token；{}=零外推终态,null=未跑完（死亡窗）——第四种"说不清"不存在；网页通知页 chips 可见
- **双发窗封死**：claim 认领式（queued→sending 单向迁移,rowcount=1 才发）——降级直发与 worker 只有一方获得发送权（短信计费敏感）
- **凭证面**：SMS 走 system_config 加密列（smtp 先例,Web 配零重启到位即通）；alerts_config 权限 admin 专属锁三处（analyst 有 system_config 不能触告警路由/计费面）
- **分层层级**：celery 任务定义归 scheduler(3)；alert_notify(2)→im_bot(3) 走 EXEMPT_UPWARD 成文豁免
- 详 docs/obsolete/任务归档/批7-告警订阅分发.md（三轮双盲审 49 条全吸收的完整契约）

## 2026-08-19 · 分钟数据源策略：XTP 自攒为主，Tusharestk_mins 产品包后启（用户拍板）

- **决定**：Tushare stk_mins 是独立产品包（2000 元/年），当前全局 1 次/小时不可用。池驱动分钟同步基础设施已建（`data_sync/pool_minute.py` + beat + 限速闸门 + API 端点）但** beat 禁用**——买包后取消注释 beat + `data_source_config.params` 配 `rate_limits` 即启用。
- **分钟数据路径**：XTP hub 订阅池标的自攒（影子期后写 bar_1min 正表），Tushare 将来作为主源、XTP 辅助校验。
- **复权因子**：已回填 95.4%（API 限速宽松），剩余 5% 部署窗口后续填即可。
- **关键实测**：stk_mins **全局** 1 次/小时（非 per-symbol）→ 30 只池日度增量需 30 小时，不可行。

## 2026-08-19 · 链条打磨：因子→实盘全链打通（四批，26 断点清零）

- **起因**：用户要求链条"打磨成熟、功能完善、体验良好+操作指导书"。探查实测 26 断点——4 整段断裂（自定义因子出不了 API 进程/因子模式实盘零下单/回测实盘频率+参数双错配/预检失败当成功）+2 UI 页失效+DSL 死功能。
- **关键决策**：①执行规则**方向感知**（R-F1：SELL=持仓口径 ALL_IN 清仓/BUY=可用资金——持仓走 ST2 position_snapshot 真相源）②DSL 实现而非删除（8 窗口函数+静默错值四形态抛异常——R-F2：错值比崩溃危险）③旧启停移除（LiveTask 唯一入口）④指导书分册+Web /help 内置都要⑤因子试算=写完即看曲线（真实 bar 喂 compute）
- **架构联动**：自定义因子三进程加载（web/celery/runner）+回测任务头 lazy 重载+factor:recalc 兼热重载钩子；全败 run 不过 F-44 验证门
- **指导书**：docs/manual 五册随 rsync 部署（server/docs/ 镜像），/api/help/{topic} + Web /help（marked 渲染）

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
- **详见**：`docs/design/D15-服务监控设计.md`（设计+职责划分+runbook）；12 号 ST4 节已加修订指针。

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

- 新待办按 `docs/obsolete/任务归档/<id>.md` 写（8 字段），做任务只读任务文件+接口契约+模块契约，零代码阅读。

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

## 2026-09-08 · 批9 内存治理三裁定

1. **zram 而非合并 worker**：风控 worker 独立是告警 SLA 的刻意隔离设计（记忆 alert-dispatch-architecture），不为省内存牺牲进程隔离；OS 层治 OS 层的问题（zram+swappiness），zram 配置入 INSTALL.md §2.4 成版本化 runbook。
2. **platform.py 改名 _platform.py（「改就彻底改」）**：三层同名占用 import 机制保留地是历史反模式，懒加载暴露绑定竞态；治本=退出争议名，守门测试固化三不变量。约定：模块名单例实例同名+包级同名 re-export 禁止再犯。
3. **前端失败语义按页分型**：无独立 catch 的 load 用 Promise.all（全成或全败）；有独立 catch 的（Logs/Strategy.loadExtra）用分立 promise 保部分失败容错，禁裸 all 短路。


## 2026-09-15 · 批21-23 三批裁定

1. **圆钮配色规则**（批21）：图标按钮一律浅蓝灰底（--el-fill-color-light）+品牌蓝图标+hover 浅蓝；颜色只表语义（danger 红/warning 橙/success 绿）；primary 蓝退出图标按钮；每卡=告警源+状态，不产生告警的信息不监控（zram 因此不入）。
2. **告警/指标两张表一个循环**（批22）：health_event=沿事件（触发/恢复有状态），system_metric=连续采样（趋势）；资源阈值 severity 存 Valkey state 键值（warn→critical 升级重发、降级只换徽标）；disk 逐挂载点判定（聚合稀释单分区爆满是漏报根因）。
3. **通知退回外部推送+留档**（批22/23）：系统 UI 只盯告警（铃铛=活跃告警数角标 admin-only）；通知中心确认机制退役（active 池只增不减，全清是唯一清空手段）；日志（task_logs）不做删除——实盘任务时间线是自愈数据源。
4. **表头位置规则**（批23）：卡片 header 左=标题，右=筛选+动作组（统一序：筛选→新建→刷新→列设置）；行筛选统一 RowFilter 弹窗复选多选（「全部」=空数组）。


## 2026-09-23 · 多账号源 D1-D6 双盲审修正（双同 PASS，13 P1 全修，3 条触首决细节）

- **双盲审结论**：双同 **PASS（无 P0）**——软件架构专家 + 量化交易高手，同章程互不可见，主会话同判比对。13 P1 + 8 P2，性质=契约遗漏/接线不明确/枚举不全/措辞失实，**无需返工**。按 [[stop-and-question-when-review-loops]] 教训不走「盲审→补缺陷→再盲审」循环，**列全真问题→一次性改→自查无矛盾**。
- **3 条触首决细节的修正**（盲审发现 + 已逐条查码核实，落 D 文档；待用户确认）：
  1. **资金基线口径**：D2-3「显式配置」→「per-account 首条快照 total_value（跟踪起点净值），显式配置仅参考展示、不作回撤分母」——回退到名义入金数会重演 9.99 亿回撤分母错配（2026-08-22 #10 口径已修过）。
  2. **account_key 唯一作用域**：单列 UNIQUE → `UNIQUE(provider, account_key)` 复合唯一（防跨券商资金账号撞号）+ 加密 account 取 API uid/自定标签（加密无资金账号）。
  3. **`_market_of` 不退役**：31号§四 曾把 `_market_of`（分项 market_op 五键）与 `_board_of`/`detect_category` 混为一谈；三者三维度，`_market_of` 是分项（etf 分项=场内基金全体、perp 需 account.provider），退役即下单闸断裂。仅退役 `_board_of`/`detect_category`。


## 2026-09-23 · 吸收外部架构两可借鉴点（用户裁定）

- **背景**：用户提供外部「量化交易系统核心架构设计规范」（机构级通用架构），比对后两条可借鉴——①品类差异化操作插件化 ②交易所/股东户维度显式建模。
- **裁定（吸收进方案）**：
  1. **品类操作插件（预留扩展点）**：可转债转股/回售、ETF/LOF 申赎等品类专属操作当前不实现，但架构预留「品类操作插件」扩展点——品类差异=数据列（属性）+插件（操作），未来加品类/操作不改顶层代码。31号 §七 从「出界」改「预留」。
  2. **exchange 股东户维度**：权限模型三维（category/board/ST）→**四维（category/exchange/board/ST）**。`security_master` 加 `exchange` 枚举列（shse/szse/bse，回填从 vt_symbol 后缀提取，可靠非前缀判断）+ `account_permission` 加 exchange 维度。根因：board=main 横跨沪/深，单 board 维度分不出「有沪股东户无深股东户」的 account。
- **不采纳**：「自动路由层」（与我们「人工显式选源、一策略一账号」方向冲突，31号 §1.1 已钉死）。


## 2026-09-23 · ST 维度降级 + detect_category 就地退役（用户裁定）

- **ST 降级**：30号/31号 原把 ST 列为「第四维正交权限」，用户质疑「为什么要关注 ST」→ 澄清三层（涨跌幅=`band_rules.pct_st` 已覆盖 / 主板 ST 适当性=唯一真权限但薄 / 退市风险=选标的排除）。**裁定：ST 从独立正交维度降级为「board=main 子布尔」**——account_permission 加 is_st 布尔（仅主板），非第四维。
- **detect_category 就地退役**：detect_category 返回 astock/crypto（因子兼容词表），与 security_master.category（stock/perp）两套词表打架。**完美方案=品类单一真源 + 因子类=派生映射**（`CATEGORY_TO_FACTOR_CLASS`: stock→astock、etf/fund/reits→etf、convertible→convertible、perp→crypto）。detect_category 仅 1 调用点（factor.py:768），就地退役成本小，纳入 D1（与 _board_of 同「前缀→数据列」模式）。


## 2026-09-24 · D1 代码双盲审 B-P0 裁定：写路径/三级时点/回填闸挂账 D5

- **背景**：D1 代码双盲审 B（交易正确性）抓 P0——D1 §五「只做」列「account_permission 读写」+「三级时点接入调用」，但实际交付只有「读」（account_allows 读）+「函数」，写路径（管理端点/seed/UI）与三级时点接线均未交付。空表 + 无写路径 = 若接线即全盘锁死。
- **裁定（挂账，非返工）**：①account_permission **写路径** ②**三级时点接入调用**（依赖 D2 的 live_task.account_id）③**board 回填完整性闸**（接线前断言 stock 全量 board 非空）→ 归 **D5**（建任务与 worker 绑定）前置；④**ST 官方名单 fail-closed** → 另批（现 namechange 派生、无档=非 ST fail-open 已知）。已落 D1 §五 + 待办。

## 2026-09-26 · 批 66a 三裁定（D26-B 切换前收束）

1. **concrete unit 整文件遮蔽替代 drop-in**（编码裁定，回写任务文件 §2.2）：install-units wrapper 只收 `*.service`（文件名正则），drop-in `.conf` 过不了安装通道——过渡注入=仓内 concrete `quant-md-hub@quant.service` 遮蔽模板实例化+`__HUB_ROW_ID__` 占位符经 release.yml 阶段 2 replace 按 inventory 注入（staging/prod 行 id 不同不可写死；replace 在指纹采集前=值变化触发单元重装）。禁入共享模板立法不变（A-P0-2 加密实例污染事故链）。66b 换名后三件套整体退役。
2. **66b 观察专项挪周二 09-29**（盲审 A-P2-1）：周一窗已承载批 64/61/65/63P4 四批+66a 顺带，第六改动同交易日归因困难——66a 顺带观察周一、66b 键切换专项周二。
3. **凭证对账读行失败=版本顺延不固化**（双盲 A-P1+编码自查同构洞）：任何「读行失败但固化新版本」的写法都会短路死锁凭证基线（首发失败固 v / 版本轮失败返新 v+旧摘要两洞同构）——统一语义「读行失败=本轮没发生，版本顺延下轮重读」；staging/prod 行 id 填值防呆注释入 inventory（勿填 EMT 行）。
