# 盲审报告 · 子代理 A（2026-08-17，P-4a 交付物）

> 双盲协议：子代理独立枚举，未读 flow/ 与 12 号稳定性设计文档；与我方矩阵在 P-4 合并，差异项为金子。
> **复核状态（主会话逐条验证）**：标注 ✅核=已独立复核代码实锤；⚠️=引用可信但未逐行复核；无标注=逻辑成立。
> 合并结果：与 F-1~F-25 收敛项不重列；新增项编号 F-26~F-59，见矩阵 §E。

## 复核结论摘要

灾难级新发现 10 项中已独立复核 8 项（F-26 事件线程裸奔/F-28 Signal 默认 0 值+聚合无归一化/F-29 风控异常归零/F-30 部分规则 KeyError/F-31 日亏拦死 SELL/F-32 JWT 默认密钥/F-33 飞书回调零鉴权/F-34 快照假值注入+db0 混装），全部属实。

---

以下为子代理原文（未删改）：

---

# 量化交易平台稳定性风险枚举（红队盲审）

## 1. 按组件分组的发现清单

### 1.1 quant-live-task@N（runner 进程 / vnpy 事件线程 / XTP 原生层）

| 组件 | 故障模式 | 证据 | 后果 | 严重级 |
|---|---|---|---|---|
| vnpy EventEngine | 事件线程一异常即死 | `venv/.../vnpy/event/engine.py:61-72`：`_process` 对 handler 无 try/except，`_run` 只捕 `Empty` | 任何一个 handler 抛异常（用户 Python 代码、PG/Valkey 故障、风控 KeyError）→ 事件线程静默退出：无 tick、无 bar、无定时器，进程仍活着，主循环照常写快照/查状态。**全系统永久失聪且无任何告警**（无 tick 看门狗） | 灾难 |
| runner 主循环"断线重连" | 死代码 | `strategy_runner/main.py:297` `hasattr(gateway,"is_connected")`；vnpy_xtp `XtpGateway`/`BaseGateway` 均无 `is_connected`（`xtp_gateway.py:187-258`、`vnpy/trader/gateway.py:33`） | 主循环重连分支永远不执行；断线全靠 vnpy_xtp 内部 `onDisconnected→login_server`（`xtp_gateway.py:291-297,489-495`），而 `login_server` 成功后**只做 query_contract+init，不重发订阅**（`xtp_gateway.py:419-435`）→ 行情断线重连后**订阅丢失，策略永久盲跑**，`_warmup_history` 补缺口逻辑也永远不触发 | 灾难 |
| XTP 凭证 client_id | 多进程同账户互踢 | `strategy_runner/main.py:42`：client_id 取自共享 broker_config（默认 1）；所有 runner 均以 `quant` 用户运行，XTP SDK 运行目录 `TEMP_DIR/xtp` 共享（`vnpy/trader/utility.py:61,72-79`，`xtp_gateway.py:413-414`） | 两个以上 live_task 绑同一 XTP 账户 → 同 client_id 后登踢前登 → 反复断连/丢 tick/会话漂移；共享目录下 SDK 流文件并发互写。旧架构 quant-strategy@N 与新 live-task 并存时更易触发 | 灾难 |
| systemd unit | 重启风暴/账户锁定 | `quant-live-task@.service`：`Restart=always`+`RestartSec=30`；`main.py:206-209` 凭证不完整 `sys.exit(1)`，`main.py:257` 交易所解析失败 `exit(1)` | 凭证失效/配置错 → 每 30s 一次 XTP 登录尝试，永不停歇；券商侧连续登录失败典型后果是**账户锁定**，人工也登不上 | 严重 |
| systemd unit | 已停任务无限 churn | `Restart=always` 对 exit 0 也重启；`main.py:129-131,277-279` 停止路径 exit(0)；`web_api/main.py:890-894` systemctl stop 失败被吞 | Web 停任务但 `systemctl stop` 失败（polkit 抖动）时，runner 退出码 0 → systemd 30s 后拉起 → 再见 stopped 再退 → 无限重启循环，journal 刷屏，unit 状态混乱 | 严重 |
| runner 停止语义 | 停止延迟 60s + 停止后仍可下单 | `main.py:271-285`（60s 才查一次 status）；`main.py:325-330` break 后 tick handler 仍注册，到 `main_engine.close()` 前窗口内 on_bar→place_order 照常发单；`XtpGateway.close()` 是 `pass`（`xtp_gateway.py:219-221`） | 管理员点"停止"后最长 60s 内策略继续交易；SIGTERM 硬杀则可能落在 send_order 与 order_log 写入之间（见 1.2） | 严重 |
| factor:recalc:trigger | 全局键被首个 runner 抢删 | `main.py:290-293`：`_r.get` 后 `_r.delete`；写入方 `scheduler/tasks.py:358` | N 个任务并存时，第一个看到的 runner 消费掉触发标记，其余任务 history 不重填 → 部分策略在坏数据上继续算因子 | 一般 |
| history 共享 list | 跨线程逻辑竞态 | `main.py:237-239`（事件线程 append/pop）vs `main.py:291,301`（主线程 `history[:] = _warmup_history`） | 重填瞬间与 on_bar 并发：bar 重复入史或丢失；暖机数据若含当前未完成分钟（同步已写入）会与 BarGenerator 内存 bar 双计 | 一般 |
| BarGenerator 尾 bar | 收盘最后一根 bar 永不触发 | `vnpy/trader/utility.py:216-223`：只有下一分钟首个 tick 到来才 push；`main.py:228-239` append 无时序校验 | 15:00 前最后一根 bar 次日开盘首个 tick 才 push，`history` 出现乱序追加，因子在陈旧 bar 上计算 → 开盘误信号。另外 `last_price=0` 的 tick 被静默丢弃（`utility.py:211-212`），停牌/一字板标的 bar 缺失无告警 | 严重 |
| 内存 | host 级 OOM | unit 注释自证 512M 被"XTP 全市场合约加载尖峰"打爆；`MemoryMax=1G`×N + web-api/celery(并发2×prefork, 各带 pandas) + PG + Valkey + safebox 共机，物理内存仅 1.8G+2G swap | 1 个以上 live_task + 同步高峰即可能触发内核 OOM，凶手不一定是 runner（可能是 PG 或 web-api）→ 全平台瘫 | 严重 |
| CPU 配额 | CPUQuota=50% + tick 洪峰 | unit `CPUQuota=50%`；EventEngine 队列无界（`engine.py:109`） | 盘口密集时段事件队列积压无上限 → 内存增长 + bar 延迟，慢而不崩 | 一般 |
| Python 代码模式 | 用户代码挂死/炸内存/状态丢失 | `strategy.py:423-448`：`exec` + `user_on_bar` 无超时无异常包裹（异常直杀事件线程，见第 1 条）；`while True` 挂死事件线程；`ctx._state` 仅内存（`strategy.py:381-387`） | 用户一行代码即可让平台失聪；OOM 重启后策略状态清零，可能立即重复开仓 | 灾难 |

### 1.2 下单路径（strategy.py / adapters.py / XTP 网关）

| 组件 | 故障模式 | 证据 | 后果 | 严重级 |
|---|---|---|---|---|
| XTPAdapter.send_order | 失败被当成功 | `adapters.py:163-167` 不检查返回值；`xtp_gateway.py:781-816` 不支持交易所/类型/两融时返回 `""`；TD 断线时 `insertOrder(session_id=0)` 返回伪造 orderid "0" | 委托从未真实发出，但 `record_broker_usage(success=True)` + signal_log/order_log 均写入 → **账面有单、实际无单**，三账对账基础失真 | 灾难 |
| place_order 顺序 | 先发单后记账 | `strategy.py:241-255`：send_order 成功后才 `_log_signal_order`（263-273） | 崩溃/SIGTERM 落在两者之间 → 交易所有单、系统无记录 → 重启后无人知道、无法撤、可能重复补单 | 灾难 |
| 订单状态机 | 无恢复、无对账、无幂等 | `adapters.py:91-98` 事件缓存仅内存；启动时不查当日委托（注释自认 XTP 网关无查委托接口，`adapters.py:84`）；重启即丢 `_cid2vt` 映射 | runner 重启后：在场挂单不可见不可撤；同一信号重复触发无任何"今日已发"幂等检查 | 灾难 |
| Signal 默认值 | volume=0.0/price=0.0 下单 | `strategy.py:27-38`（默认 0.0/0.0）+ `strategy.py:229-231`（`is not None` 判断形同虚设）→ DSL/因子聚合信号 volume=0、price=0；`volume_type=PERCENT/ALL_IN` 全无实现 | DSL 策略实盘每根 bar 发 0 股@0 元废单被 XTP 拒绝但记为成功订单 → 无效单洪泛 + 日志表膨胀 + 券商侧可能限制 | 灾难 |
| 信号聚合无归一化 | 每根 bar 都出 BUY | `strategy.py:49-60`：RSI(0-100) 等原始值直接 weighted_sum 对比 ±0.3 阈值；`risk.py:44-48` 的 `max_trades_per_day`/`single_position_pct` **从未被检查**（`_check_etf_conv` 只查单笔金额，`risk.py:189-203`） | 单 RSI 因子权重 1 时 score≈RSI>0.3 恒成立 → 子类策略每分钟发一单，日频次/仓位上限皆不存在 | 灾难 |
| cancel_order | 退化路径必失败 | `adapters.py:169-183`：缓存无此单时用 client_id 构造 CancelRequest | 网关回单事件未到（或重启后）时撤单请求发的是错误 orderid → 撤单失败无人重试 | 严重 |
| _wait_update | 永等满 2s 返回陈旧值 | `adapters.py:223-230`：key 集合不变（同账户重复查询）即等满超时 | 每次查询多耗 2s（主循环阻塞）；TD 断线时 `xtp_gateway.py:846-860` 查询静默 no-op，返回上一轮陈旧账户/持仓 | 一般 |
| `_cid_seq` | 无锁自增 | `adapters.py:151` | 多线程下单时 client_id 重复（当前单事件线程，潜在） | 一般 |

### 1.3 风控 RiskControl

| 组件 | 故障模式 | 证据 | 后果 | 严重级 |
|---|---|---|---|---|
| _get_global_state | **fail-open** | `risk.py:256-257`：任何异常返回全零 RiskState；快照来源 `main.py:309` 断线时兜底写入 `initial_capital` 当 total_value | PG 不可用/表被删/磁盘满时：回撤、日亏损限制**静默归零失效**，交易照常进行——风控在故障时比平时更松 | 灾难 |
| 规则加载 | 进程级冻结 | `risk.py:69,94-98`：单例构造时读一次 DB；Web 的 risk_rules CRUD（`web_api/main.py:2185-2210`）只写 DB；`update_rules`（`risk.py:274-281`）纯内存且无端点调用 | 运行中的 runner **永远看不到风控参数修改**，调紧限额必须逐个重启实盘任务——管理员以为已收紧，实际无效 | 严重 |
| 规则字典 | 部分 key → KeyError | `risk.py:88-90` 返回部分 dict；`risk.py:191,207` 直接 `self._rules["etf_conv"]/["crypto"]` | 管理员只建一条 global 规则（Web 完全允许）→ 每笔场内单 check_order 抛 KeyError → 直杀事件线程（见 1.1 第 1 条） | 灾难 |
| 日亏损限额 | 连止损平仓也拦 | `risk.py:179-180` 文案"仅平不开"，实现 `approved=False` 拦截一切订单（含 SELL）；`strategy.py:233-235` 无差别 return | 触发日亏损限额后**无法减仓**，只能眼睁睁看着亏损扩大，且无自动撤单 | 灾难 |
| 熔断 | 不撤在场单 | `risk.py:106-110` 只置 redis 标志，check_order 前置拦截新单 | 熔断后已挂出的限价单继续有效并可成交 → 熔断期间仍建仓 | 严重 |
| Valkey 依赖 | 熔断检查依赖 Valkey | `risk.py:102-104`：`is_halted()` 直读 redis，异常上抛未捕 | Valkey 断连时每笔信号抛异常 → 订单不发（fail-closed 尚可）但异常直杀事件线程（失聪） | 严重 |
| 品种分类 | 前缀表不全 | `risk.py:132-137`：ETF 只认 51/15/56 开头；588 科创 ETF、58 新段 → 归入 astock | 在 etf 关、astock 开的配置下，ETF 订单绕过分项开关被放行 | 严重 |
| 杠杆/金额检查 | 死代码与旁路 | `risk.py:208`：order dict 从无 leverage 键（`strategy.py:226-232`）→ 恒为 1；`risk.py:197` `price>0` 才截断 → price=0 单绕过金额上限 | 杠杆上限形同虚设；0 价格单不做金额限制 | 一般 |

### 1.4 Web API（认证 / 启停 / 飞书路由）

| 组件 | 故障模式 | 证据 | 后果 | 严重级 |
|---|---|---|---|---|
| JWT 默认密钥 | 可伪造任意角色 token | `auth.py:27-29`：默认 `quant-dev-secret-change-me` 仅告警；`crypto_utils.py:20-27`：ENCRYPTION_KEY 缺省时从 JWT_SECRET 派生 | .env 未设密钥（或从模板复制）→ 任何人可伪造 admin token 全控平台（启停策略/熔断/改配置），并可解密 PG 中全部券商凭证 | 灾难 |
| verify_jwt | 禁用/删除用户 token 仍有效 | `auth.py:84-99` 只验签名+黑名单；`enabled/deleted_at` 只在登录时查（`auth.py:198-200`） | 被禁用用户最长 24h 保留完整 API 权限（含 trader 的启停/熔断） | 严重 |
| ensure_default_admin | 已知口令复活 | `auth.py:249-264`：users 表空/旧格式哈希 → 创建/重置 admin/admin123 | PG 从备份恢复或表被清后，每次 web-api 启动都会植入已知口令的管理员 | 严重 |
| start/stop live_task | systemctl 失败被吞 | `web_api/main.py:876-880,890-894`：异常仅 log，仍返回 running/stopped | 幻影状态：DB 说 running 进程没起（或反之）；配合 Restart=always 形成重启循环（见 1.1） | 严重 |
| verify 端点 | 回测门禁可一键绕过 | `web_api/main.py:741-748`：直接置 `backtest_verified=true`，无任何回测证据要求 | "未通过回测禁止实盘"这一第三级开关对任何 trader 是摆设 | 严重 |
| 任务查重 | 同策略同标的可重复建任务 | `web_api/main.py:802-862` 无 (strategy_id,symbol) 查重 | 两个 live_task 同标的同策略 → 双倍下单（每任务各一套信号） | 严重 |
| 飞书 webhook/card | 无认证的操作执行入口 | `router.py:55-77`：card/callback **完全无签名校验**；`bot.py:148-154`：未配 verification token 时 webhook 签名校验直接放行；`bot.py:271-305`：可执行 emergency_halt/risk_resume/strategy_start/strategy_stop；卡片注释声称 60s 超时但**未实现时间戳**（`bot.py:159-178`） | 若 nginx 暴露 /lark 前缀：任何人 POST 即可 resume 熔断/拉起策略；卡片确认可永久重放 | 灾难 |
| 飞书角色模型 | 发消息者即角色 | `bot.py:186-198`：fid 路径只查机器人 role，**不校验 open_id 白名单** | 任何能给该机器人发消息的飞书用户都拥有该机器人的角色权限，操作类工具经 LLM 可达 | 严重 |
| CORS | `allow_origins=["*"]` + credentials | `web_api/main.py:63-69` | 任意源可携凭证调用 API（JWT 在 header，风险取决于前端存储方式，仍属暴露面） | 一般 |
| /api/reconcile | viewer 可同步执行+触发告警 | `web_api/main.py:2297-2301`：`apply().get()` 在 API 进程内执行 | 低权限刷告警/DB 扫描 | 一般 |
| verify_jwt Redis | 每请求新建连接 | `auth.py:96,114` | Valkey 故障时**所有**需认证端点 500（API 全瘫）；高并发 fd 压力 | 一般 |

### 1.5 Celery / beat / 数据同步

| 组件 | 故障模式 | 证据 | 后果 | 严重级 |
|---|---|---|---|---|
| beat schedule 文件 | 放 /tmp | `quant-celery-beat@.service`：`--schedule /tmp/quant-celerybeat-schedule` | 重启/tmp 清理后 last-run 丢失 → beat 启动即**全部定时任务同时到期**：全市场分钟同步+各 daily 一起打 Tushare（限频风暴）+ 队列挤压 | 严重 |
| worker 队列拓扑 | 单 worker 承载全部队列 | `quant-celery-worker@.service`：`-Q celery,risk,data,analysis`，并发默认 2（`app.py:34`） | 一个 70 分钟级全量同步（`tasks.py:481-483` soft_time_limit=3600）占满 worker → risk_sweep/email_outbox_sweep/对账**全部饿死**，风险与告警任务停摆数小时 | 严重 |
| data_sync_scheduler | 游标在未来则永久停摆 | `scheduler/tasks.py:452-460`：`base=last_sync_ts`，croniter 算 next > now 即跳过 | 时钟回拨/手工改大 last_sync_ts 后该同步**静默永不触发**，数据陈旧无告警（依赖人看 sync_log） | 一般 |
| tushare 楼取 | 异常吞成空数据 | `tushare_adapter.py:85,118` 等多处 `except Exception: return 空`；engine 视空为"无数据" | 积分耗尽/接口封禁表现为"市场今天没数据"而非报错；runner `_warmup_history`（`main.py:72-90`）无新鲜度校验 → **在陈旧分钟线上算因子下单** | 严重 |
| 交易日历回退 | 假缺口 | `data_sync/engine.py:619-648`：查不到 trade_cal 回退工作日 | 节假日被当缺口反复回补，浪费配额+告警噪音 | 一般 |
| SyncLock 心跳失败 | 不中止持锁任务 | `sync_lock.py:66-68`：心跳异常只置标志 | Valkey 抖动后锁被他人抢走，两个同步并发跑同一 sync_id | 一般 |
| broker_health_check | 未配置即告警 | `tasks.py:804-820` + `broker.py:48-50`：`cls()` 无凭证 → test False → notify | 每次巡检对未配置的通道误报告警 → 告警疲劳，真告警被忽视 | 一般 |
| reconcile/drift_check | 永真告警 | `tasks.py:272-279`：order_log.status 恒为默认 'submitted'（迁移 0027:119，全库无 UPDATE）；trade_log **全库无任何写入方**（grep 证实） | "委托不成交"对历史每一单每小时重复告警 → 铃铛噪音化 | 严重 |
| 时区/时钟 | 本地时间判断交易时段 | `scheduler/tasks.py:22-28` `datetime.now()` | 服务器时钟漂移 → 同步/告警在错误时段跑；无 NTP 监控 | 一般 |

### 1.6 存储（PostgreSQL / Valkey）

| 组件 | 故障模式 | 证据 | 后果 | 严重级 |
|---|---|---|---|---|
| trade_log | 无人写入 | 全库 grep 无 INSERT；持仓页 `web_api/main.py:929` 从 trade_log 聚合 | 持仓页永远空 → 人工核对通道缺失，实际仓位只有 XTP 内存知道 | 灾难 |
| account_snapshot | 多任务混写 + 假值注入 | `main.py:306-322`：无 task/account 维度，N 个 runner 每分钟各插一行；断线时 `total=initial_capital` 兜底（309）；当日基准取"全表今日第一行"（313-318） | 风控全局状态（`risk.py:245-255`）读到的是**任意任务写入的可能造假、可能跨账户**的值 → 回撤/日亏损计算失真；且首个快照若是假值，全天 daily_pnl 基准错误 | 灾难 |
| Valkey 单 db 混用 | broker/缓存/熔断/锁/JWT 黑名单同库 | `.env:8` `VALKEY_URL=.../0`；`scheduler/app.py:17,39-40`（celery broker 同 URL）；`risk.py:66`、`main.py:22-29` | 与描述的 db2/db3 分离不符：一次 FLUSHDB / OOM / 故障同时带走 celery 队列、熔断标志、同步锁、JWT 黑名单——**交易系统与消息总线共死** | 严重 |
| 连接池 | 进程各自 10+20 | `db.py:31-37` | N runner + worker 子进程 + web-api 并发 → PG max_connections 逼近 → check_order 变慢/失败 → 叠加 fail-open（1.3） | 一般 |
| bar_1min 体量 | 全市场分钟线无保留策略 | `engine.py` 分钟同步全市场；disk_monitor 仅 85% 告警（`tasks.py:387`） | 小盘机器 PG 膨胀→磁盘满→写入全败（且此时风控 fail-open） | 一般 |
| ensure_table | 进程内集合跳过 DDL | `db.py:53-61` | 表被删后同进程不再重建（设计如此，但依赖 verify_schema 告警，而 runner 不跑 verify_schema） | 一般 |

### 1.7 外部依赖（XTP/SMTP/飞书/Tushare/LLM）

| 组件 | 故障模式 | 证据 | 后果 | 严重级 |
|---|---|---|---|---|
| XTP MD | 断线重连不重订阅（见 1.1） | `xtp_gateway.py:419-435` | 静默失明 | 灾难 |
| XTP TD | 断线期间查询静默 no-op | `xtp_gateway.py:846-860` | 快照写假值（1.6） | 严重 |
| 健康检查盲区 | 不探测 XTP/runner/tick 新鲜度 | `tasks.py:147-182` 只查 PG/Valkey/LLM | 核心交易链路（XTP 连接、事件线程存活、tick 是否还在来）**没有任何监控** | 严重 |
| notify 依赖 Valkey | 告警与被监控对象共死 | `notify.py:56-60` redis 调用无容错 | Valkey 挂时恰恰是所有告警该响的时候，一条也发不出；外部推送仅 risk+critical（43-45），通道发送失败只记 log（98-101） | 严重 |
| LLM 健康探测 | 每 beat 花真实调用 | `tasks.py:169-174` | 配额消耗+噪音 | 一般 |
| SMTP | 成功≠送达（发件箱重试已有） | `email_service.py`（结构） | 邀请/重置类邮件失败只进站内通知（仅 admin 可见） | 一般 |

### 1.8 部署与变更轴

| 组件 | 故障模式 | 证据 | 后果 | 严重级 |
|---|---|---|---|---|
| deploy-server.sh | 重启清单不含 live-task | `scripts/deploy-server.sh:18-27`：restart-server/celery/feishu，无 quant-live-task@N | 部署后 runner 跑旧代码 + web/celery 跑新代码 + alembic 已迁移 → 跨进程 schema/行为漂移；runner 进程内懒加载的新 import 可能拿到半新半旧代码 | 严重 |
| rsync 整目录 | 运行中进程的包被替换 | 同上（rsync 到运行目录） | .pyc 失配/ImportError 随机出现于下一个懒加载点 | 一般 |
| 迁移时序 | migrate 在旧 runner 仍写旧表结构时执行 | deploy 顺序：pip → migrate → restart（不含 runner） | 旧 runner 写已变更列 → 写入失败 → 快照/日志缺失（叠加 fail-open） | 严重 |
| 交易时段部署 | 无冻结窗口机制 | 全部脚本无时段判断 | 盘中 restart celery → 回测/同步被 SIGKILL（time_limit）；restart web-api → 停止/熔断按钮不可用窗口 | 一般 |
| strategy 编辑语义 | 双轨不一致 | 新 live_task 用创建时快照（`main.py:844-851`），旧 quant-strategy@N 每次启动读最新配置（`main.py:148-163`） | 运维者对"改策略是否生效"的心智模型在两架构间相反；stop/start 同一 live_task 仍是旧快照，但 UI 无此提示 | 一般 |

## 2. 红队攻击路径清单（如何亏钱 / 重复下单 / 失控 / 失明）

1. **重复下单（最短路径）**：同一策略同一标的建两个 live_task（`web_api/main.py:802-862` 无查重）→ 每根 bar 双倍信号双倍下单；或掐准 send_order 与 order_log 写入之间的崩溃窗口（`strategy.py:241-273`，systemd SIGTERM 即可制造）→ 重启后无任何幂等/对账（`adapters.py:84,91-98`）→ 补单。
2. **让风控消失**：让 PG 短暂不可用（或把磁盘塞满）→ `_get_global_state` fail-open（`risk.py:256-257`）+ 快照写入失败 → 回撤/日亏损限制全部归零，交易继续；顺带 Valkey 抖动可让 is_halted 抛异常（fail-closed 但杀事件线程）。
3. **让它失控**：
   - 触发日亏损限额 → SELL 也被拒（`risk.py:179-180`）→ 无法止损；
   - 熔断 → 在场限价单不撤（`risk.py:106-110`）继续成交；
   - Restart=always + 停不掉的 unit（systemctl 失败被吞 `main.py:890-894`）→ 已停策略反复复活；
   - POST /lark/card/callback 伪造确认（`router.py:55-77` 无鉴权）→ risk_resume / strategy_start。
4. **让它失明**：任何一路异常（用户因子 KeyError、风控 partial-rules KeyError、Valkey 闪断）→ vnpy 事件线程死亡（`engine.py:61-72`）→ 无 tick 无 bar 无告警，进程与 systemd 全绿；或等一次 XTP MD 断线重连 → 订阅不重发（`xtp_gateway.py:419-435`）→ 同样全绿地盲跑。两路都是**零成本、无入侵**即可达成。
5. **账面欺骗**：send_order 返回 ""/伪造 id 也记成功（`adapters.py:163-167` + `xtp_gateway.py:781-816`）+ trade_log 永不写 + 持仓页从空表聚合（`main.py:929`）→ 操作者看到的持仓/订单与真实完全脱节，基于假账人工干预。
6. **权限夺取**：.env 缺 JWT_SECRET/ENCRYPTION_KEY（默认派生链 `auth.py:27` → `crypto_utils.py:20-27`）→ 伪造 admin token → 全控 + 解密券商凭证；或等一次 PG 恢复 → admin/admin123 自动复活（`auth.py:249-264`）。
7. **磨死它**：写一个含 `while True` 或抛异常的"自定义 Python 策略"（analyst 角色即可创建，`strategy.py:423-448` 无超时）→ 事件线程挂死 → 全任务失聪；写一个内存炸弹 → MemoryMax 内反复 OOM-重启循环，状态清零重复开仓。

## 3. 我认为最容易遗漏的 5 个冷门风险

1. **vnpy EventEngine 无异常保护**（`vnpy/event/engine.py:61-72`）：所有人都会自查自己代码的 try/except，没人会想到"框架的事件线程已经死了"。这是本系统所有下单路径异常的共同放大器：一次小异常 = 永久失聪。
2. **`hasattr(gateway, "is_connected")` 恒为 False**（`strategy_runner/main.py:297`）：断线重连分支从未执行过一次，配套的"断线补缺口"暖机同样从未生效。看起来"写了重连"，实际是死代码；而真正重连（vnpy_xtp 内部）又不重订阅。
3. **`factor:recalc:trigger` 是全局单消费者键**（`main.py:290-293`）：多任务场景下数据修复通知被第一个 runner 吃掉，其余任务继续用坏数据——单任务测试永远发现不了。
4. **account_snapshot 的 `initial_capital` 兜底**（`main.py:309`）：TD 断线/查询超时的那一刻，系统把"初始资金"当成"当前总资产"写进风控数据源（`risk.py:245-255`），回撤瞬间"归零回正"——风控被自己的兜底逻辑麻醉。
5. **XTP 共享 client_id + 共享 `~/.vntrader/xtp` 目录**（`main.py:42`、`utility.py:72-79`）：多 live_task 是"每任务一进程"的设计卖点，但 XTP 侧它们是同一个客户号——互踢会话 + SDK 运行文件互写，只在"第二个实盘任务启动"那一刻爆发。

（第 6 候补：celery beat schedule 放 /tmp，重启后全量任务同时到期风暴。）

## 4. 共享行情 hub 架构（计划中）新增风险专节

改造本身方向合理（消除 N 份全市场合约表与 N 个 XTP 会话），但引入以下新失败面，需在设计中前置：

| 新失败面 | 具体风险 | 设计要求 |
|---|---|---|
| **hub 单点** | hub 死 = 所有策略同时断粮；hub 重启窗口内产生的分钟 bar 永久缺失（XTP 不回推） | hub 需 WatchdogSec + 启动后**从 PG 补齐缺口 bar 再广播**；worker 必须能区分"hub 说没有"与"hub 不在" |
| **消息丢失** | 若分发走 redis pubsub（项目现状倾向），at-most-once：worker 重启/重连的瞬间 bar 丢 → 策略在缺 bar 的序列上算均线/金叉，结果错但无异常 | 每 bar 带序号，worker 检测断序即自愈（PG 补拉）或显式降级拒交易；宁可 stream/list 带 ack |
| **重复/乱序** | hub 重发、worker 重连重放、或旧 hub 复活 → 同一 bar 两次送达 → 同一根 bar 两次 on_bar → **两次下单**（现有 place_order 无 bar 级幂等，`strategy.py:187-202`） | worker 按 (symbol, ts) 去重是硬前提，否则 hub 化直接放大重复下单风险 |
| **陈旧数据交易** | worker 无从感知 bar 的产生时刻 vs 到达时刻；hub 积压时 worker 在几分钟前的价格上交易（限价单瞬间偏离市场） | 消息必须携带交易所时间戳+hub 发送时间戳，worker 超龄即丢弃并告警；慢消费者要背压/丢弃而不是无限排队（现状 EventEngine 队列无界即是先例） |
| **旧 hub 复活（split-brain）** | 两个 hub 进程同时持 XTP 连接：同 client_id 互踢只是其中一种结局；更险的是旧 hub 用**陈旧行情**继续广播，worker 无从分辨 → 在旧价格上交易 | 需要 Valkey 租约（可复用 SyncLock 的 token 校验模式，`sync_lock.py:44-49`）+ **fencing token**：每条消息带代次号，worker 拒绝旧代次——单纯 SET NX 锁不够，锁过期后旧进程还活着 |
| **迁移期共存** | 旧 quant-live-task@N（自带 XTP）与新 hub 同账户并行 → 1.1 的互踢问题在迁移窗口集中爆发 | 迁移顺序必须"先全停旧 runner，再起 hub"，且 hub 用独立 client_id |
| **停止/熔断语义漂移** | 现在 halt 在每个 worker 的 check_order 里生效；hub 化后若下单也经 hub 转发，熔断必须同时到达 hub 与 worker 两层，否则出现"hub 还在替已熔断账户发委托"的错位 | 熔断状态读取点、撤单责任（谁负责撤在场单）要在 hub/worker 间显式划界 |
| **资源重分布** | 512M+ 的合约表尖峰移入 hub（hub MemoryMax 要单独定量）；N 个 worker 的 socket/fd；1.8G 内存下 hub+worker 总量可能不降反升 | 容量测算先行；worker 进程要远小于今日 runner（否则改造成本无收益） |
| **PG 依赖加深** | worker 重启暖机若改为依赖 PG 分钟线：Tushare 分钟同步是**盘后**的（`engine.py` 调度），盘中重启拿到的是昨日数据 → 因子全错 | 盘中暖机应向 hub 请求回放（hub 缓存当日 bar），PG 只做隔日兜底 |

## 总结

本系统最大的稳定性敌人不是某个单点故障，而是三个系统性放大器：(1) vnpy 事件线程零异常保护——一切小异常都被放大为永久静默失聪；(2) 风控链路的 fail-open 与数据源污染（PG 异常归零 + initial_capital 兜底快照）——故障时保护恰好消失；(3) 订单账实分离（send 前不记账、trade_log 无人写、无幂等无对账）——崩溃窗口与重复任务直接转化为真实资金风险。建议任何优化（包括 hub 化）之前，先补：tick 看门狗、事件线程存活监控、check_order 全链路 fail-closed、订单幂等键与启动对账。
