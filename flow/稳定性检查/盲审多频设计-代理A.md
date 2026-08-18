# 盲审 · 多频数据设计 · 代理 A（2026-08-18，数据语义视角）

> 评审对象：多频率数据供给设计（会话内提案）。结论：**方向正确（分层/截至昨日/断言），但站在两块已塌的地基上（F1 复权因子全 NULL、F2 日线写入断言自锁+游标漂移）；F3/F4 是契约文本一句话能锁死的一致性漏洞**。

## 致命
- **F1 复权前提虚构**：bar_1D 的 adj_factor 所有主流写入路径恒 NULL（`_daily_to_rows` 第 10 字段硬编码 None；`pull_cb_daily` 显式 None；唯一带因子的 `pull_daily` 是 pro_bar adj="qfq" 已复权价，与未复权行混存同一表）。触发：resample 对 None 做除法崩/静默垃圾；修复路径补过 qfq 行的标的日序列在复权口径间跳变。修：数据层立契约"未复权价+逐行 adj_factor（缺省 1.0）"，beat 补 pro.adj_factor 回填；repair 路径改 adj=None；暖机加非空断言。
- **F2 日线写入断言自锁**：`_VALID_FREQS` 小写 '1d' vs 写路径 "1D" → assert 必炸；异常在 `_sync_by_trade_date` 被逐日吞成 failed_dates，`last_sync_date` 照样推进——**写入零行游标照走，事后增量补不回**。自 08-13 bar_1D 冻结。修：先修 freq key；服务器 sync_log 核实冻结区间，按缺口手动回补（游标已漂移）；设计加防线：日界重查后断言 `daily 最末日 == trade_cal 上一交易日`。
- **F3 "截至昨日"时钟未定义**：墙钟 vs 当前 bar 的交易日，在夜间回放下分叉——周一深夜回放上周五 session，墙钟昨日=周一 → 周一日线行（未来数据）进 ctx.daily，回测的 ≤D-1 断言却会拦住同一数据。修：**cutoff 一律由当前 bar 的 ts 日期推导（ts < bar_date）**，暖机/日界沿/回测共用同一函数，≤D-1 断言 live 也跑（违反即告警）。
- **F4 回测硬编码 1D 回放**：`tasks.py:683 get_bars(symbol,"1D")` 写死、`create_backtest_api` 无 freq 参数——实盘 ctx.history=分钟、回测=日线，同一因子两个世界语义分裂（既有裂缝，多频放大）。修：契约+backtest_verified 门槛显式声明"当前回测只验证日线因子；消费分钟语义的策略不可标 verified"；根治=1min 回放通道（另立任务）。

## 严重
- S1 日界沿单发且在 `_guard` 吞异常区：一次 PG 抖动=整天日线冻结无告警；beat 缺行时周月桶静默短桶。修：改惰性校验（每 N 根 bar 比对 daily 最末日 ≥ 上一交易日，不符重查+限频告警），沿只作提前刷新优化。
- S2 改动清单漏 strategy.py：BarContext 两处构造点在 strategy.py（189/371），Python 策略拿的是 StrategyContext——不加则目标用户摸不到 ctx.daily。
- S3 `factor:recalc` 触发只刷 1min history 不刷 daily（direct main.py:612 / hub gen_jump rewarm 同）——补采意义丢一半。修：rewarm/recalc 统一走多频刷新。
- S4 resample 窗口错配：250 日≈50 周，丢尾后 52 周均线静默降级；`resample("Y")` 恒空；首桶（窗口起点周三）也是残根但只丢尾根。修：残根判定补头部；needs 校验按 resample 目标推下限，注册期报错。
- S5 滚动窗 qfq 锚定=历史周月 bar 逐日重述（除权日进出窗口，同一历史周今明值不同）。修：契约写死锚点规则，回测实盘同函数；signal_log 记录窗口末日 adj_factor 供审计。
- S6 缺省语义未定义：None vs []（TypeError vs IndexError 之别）。修：未声明→None（注册期禁引用）；声明无数据→[]（触发告警）。
- S7 Phase 2 order_book 因子天然不可回测（tick_l1 启用日起积累），与 backtest_verified 三级开关冲突。修：验证器对 needs order_book 显式标 live-only，闸门单独一档。

## 一般/陷阱核对
- ts 类型三混：TIMESTAMPTZ 读出 aware / vnpy bar naive / hub ISO 字符串——日界/resample 的 date() 必须统一 `tz_convert('Asia/Shanghai')` + PG timezone=UTC 环境测试
- direct=分钟首标注 vs hub=分钟末标注：共用日界工具只许看 date()，禁 HH:MM；日界沿必须在 ts 去重之后（回放重复 bar 白触发）
- crypto 无日线源：品类校验直接拒绝 crypto 声明 1d needs
- factor_def 无 needs_daily 列：需 alembic 迁移（铁律运行时禁 DDL）
- needs 双入口（params dict vs register_factor 参数）必漂移：统一 needs dict
- IPO<250 日短窗：契约写明短窗不报错

## 简化机会
- 回测不复用"沿+重查"：一次性预载全区间日线，每 bar 内存切片 `ts < bar_date`——与 live 严格同构、零逐 bar PG 往返
- resample 缓存键 `(freq, daily 末行 ts)`
- 残根判定免交易日历：`date(昨日)` 与末桶周/月归属比较即可

（裁定与修订见 16 号设计 v2；F1/F2 先修——F2 已随 2026-08-18 15:06 部署，F1 立数据回填任务）
