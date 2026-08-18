# 盲审 · 多频数据设计 · 代理 B（2026-08-18，集成与架构视角）

> 评审对象：多频率数据供给设计（会话内提案）。结论：**分层与"日线截至昨日+回测断言"口径正确，性能无虞；但 F1/F2 两块地基（复权数据、日线新鲜度）在当前代码/数据里不存在或已断裂，接口改动清单缺 strategy.py 构造点与 on_bar 通道定义**。先补 F1/F2 再谈实现。

## 致命
- **F1 adj_factor 全库 NULL**（本机 psql 实测 13,397,992 行非空 0）：`_daily_to_rows` 第 10 字段硬编码 None；`pull_cb_daily`/`pull_minute` 显式 None；无任何 adj_factor 回填任务。若日后用 qfq 填补=前视+与 XTP 原始分钟价口径混。修：Tushare pro.adj_factor 落库（后复权约定、append-only 不漂移），或 v1 契约明写"不复权恒 None+禁跨除权日因子"。
- **F2 日线夜间同步当前就是断的**：`_VALID_FREQS` 小写 vs 写路径 "1D" → save_bars assert 必炸（a28a5fa 2026-08-13 引入；本机 sync_log 最后成功 08-07）；per-date try/except 吞成 failed_dates、游标照推。多频首日 09:31 日界重查拿到断更数据，因子静默用过期窗口。修：统一 freq key + 验收"昨晚的 bar_1d 今早在"；日界重查后断言 daily 最末日==上一交易日，不符告警。

## 严重
- S1 needs 三源优先级未定义+浅合并整体覆盖：`main.py:326 params={**base,**task}`——任务级 needs 替换掉策略级 needs 的 "1d" 键；Python 模式声明 needs 的唯一通道就是 params。修：砍任务级 needs，注册表聚合+策略级 params。
- S2 ctx 二义：契约挂 BarContext 还是 StrategyContext 没写清；StrategyContext 不加则 Python 策略（目标用户）摸不到 daily。修：双暴露（StrategyContext 委托 BarContext）。
- S3 daily 进 BarContext 的通道未定义，且 crypto_cta_trend/convertible_doublelow 覆写 on_bar 自建 BarContext——基类顺手注入的实现在覆写者上失效。修：钉死 `on_bar(bar, history, daily=None)` 仅关键字+逐调用点列出。
- S4 回测快路径现状=日线（`tasks.py:683` 写死 1D）：铁律"回测实盘一致"在快路径本就不成立，多频把隐性裂缝变结构性。修：写明 history 按模式二态或回测切 1min；ctx.daily 口径（≤D-1 两模式一致）作为唯一对齐锚。
- S5 日界沿重查异常隔离：查库失败在 `_guard` 内会连 bar 处理一起丢（信号+bar_shadow 全丢）。修：重查自裹 try/except，失败退旧 daily+告警，重查放 on_bar 前只做一次；direct 在事件线程内做超时控制。
- S6 市场覆盖：无 crypto 日线源、日界定义不同（UTC 跨零点）。修：v1 scope=A股系三品类；needs 命中无源市场启动即告警拒跑。
- S7 选股引擎泄漏：`static_only` 只过滤 needs_history==0，needs_daily 因子会混进选股清单（选股路径无 ctx.daily）。修：过滤条件扩 `needs_daily==0`。

## 一般/陷阱核对
- 残根无日历不可判定；trade_cal 在但可能只近年——"恒丢末组"是自洽简化（月线最多滞后一月）需写进契约；W 用 W-MON 非 W-SUN
- 250 交易日≠timedelta(250)：只够 ~170 交易日，换算用交易日历或 days≈380
- factor_def 加 needs_daily 列需 alembic 迁移 + load_factors_from_db/register_custom_factor + Web 表单三处同步
- 回测日界复用"重查"而非切片=前视；落点钉死切片实现
- factor:recalc 只刷分钟 history 不刷 daily：可接受，写明
- Phase 2 缺 direct 模式五档来源（BarGenerator 触发拿不到末 tick，需 on_tick 缓存）；tick_l1 走 alembic；回测启用日前无 order_book，因子必须可缺省。已核对：流消息可选字段对老 worker 安全；hub 直写 PG 有先例
- 部署与影子期交互：多频恰改 on_vnpy_bar/handle_msg——影子期要验证的正是这两个函数，明晨 diff 偏差无法归因。除非"无 needs 声明字节级零行为变化"，否则影子门禁过后再上
- 性能核对通过：日界沿 1 查询/日/策略+暖机 1 次；250 行≈75KB/策略；索引在
- 兼容核对通过：BarContext 全部构造点全关键字传参，加 daily=None 不破坏 252 基线

## 简化机会
- 砍任务级 needs 通道（注册表聚合+策略级 params，连同快照漂移一起消失）
- 日界沿共用函数钉死签名：`maybe_refresh_daily(bar_ts, state, loader) -> bool`，三处只注入 loader（direct/hub=PG、回测=切片）
- resample 单 bar 缓存先不做（250 行聚合亚毫秒），测出热点再加

（裁定与修订见 16 号设计 v2；F1/F2 先修——F2 已随 2026-08-18 15:06 部署，F1 立数据回填任务）
