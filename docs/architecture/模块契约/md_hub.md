# 模块契约 · md_hub（共享行情 Hub，ST7 2026-08-17）

> 设计：`docs/architecture/14-共享行情hub设计.md` v2；需求：13 号。纯数据面（无下单/无风控）。
> 批 2（2026-08-25）主循环迁上 `strategy_framework/runtime/` 骨架：EngineLoop 到期驱动钩子（废 counter%N 相位耦合），L2 会话自愈收编 MdSessionSupervisor——行为值不变（确证差异见「行为差异」节）。

## 文件结构

```
server/src/md_hub/
├── main.py    # 入口 + 钩子编排（批 2 重写 629→336 行）：租约/守卫接线/EngineLoop 注册/事件 handler
└── parts.py   # 数据面部件（批 2 原样移驻，字节级一致）：聚合器/PG 写/租约/ThinGateway/latest_tick
```

parts.py 的公共件在 main.py 顶部 import 重导出——既有测试导入路径（test_hub_arch/test_stock_detail）不受影响。

## Public API

**main.py**

| 符号 | 签名 | 说明 |
|---|---|---|
| `main()` | 入口 `python -m src.md_hub.main` | systemd `quant-md-hub@quant` 单实例 |
| `LATEST_TICK_PREFIX` | 重导出 | test_stock_detail 经 main 取用 |

**parts.py（数据面部件）**

| 符号 | 签名 | 说明 |
|---|---|---|
| `MinuteAggregator` | `on_tick(symbol, tick) -> dict\|None`；`flush_all() -> list[dict]`；`flush_symbol(symbol) -> dict\|None` | 分钟聚合（分钟末标注/累计差分含冷启动基线/跨日清零/untrusted 双门限+收盘桶豁免）；flush_symbol=退订前防丢在桶最后一分钟 |
| `_PGWriter` | `push(bar)`；daemon 线程 | bar 批量落库（10s 批/有界队列 5000 溢出丢最旧，不反压分发） |
| `_lease_boot(r)` / `_lease_acquire(r)` | `-> (uuid, gen)` | 租约启动（先拿权再连行情）：3 次重试；真让位 SystemExit(3)，耗尽 os._exit(4)；区分存储不可达与 NX 失败 |
| `_write_latest_tick(r, symbol, tick, fail_ts)` | | 最新 tick 快照（0 价过滤前置/连败 60s 退避防半死 Valkey 拖死主链） |
| `ThinGateway` | BaseGateway 子类 | 仅事件转发；connect/send_order 等抽象方法全 stub（数据面禁交易 R-HALT1 代码级保证） |
| `_project_symbol(tick)` | TickData→`600000.SHSE` | vnpy SSE→项目 SHSE |
| `LEASE_KEY`/`GEN_KEY`/`SURRENDER_KEY`/`_LEASE_RENEW_LUA` | 常量 | 租约三键 + Lua CAS 续期脚本 |

## 行为契约

- 分发：`XADD hub:bars:{symbol}` MAXLEN~5000，字段 `gen/seq/ts/pub_ts/untrusted/ohlc/volume/amount/tick_count`；seq 成功后才占号（失败不留洞）；事件线程 on_tick 与主循环 flush 经 seqs_lock 互斥
- 最新 tick：`SET hub:latest_tick:{symbol}` TTL 65s（三档项 12）——价量+五档+涨跌停，每 tick 写；断流 65s 自动过期（消费方 `stock_detail._quote_block` 降级腾讯源）
- fencing：租约 `hub:lease`（SET NX EX30 + Lua CAS 续期 5s 一续）；`gen = INCR hub:gen` 永不回退。**退出码**：3=租约让位（unit StartLimit 接管）/4=启动重试耗尽/5=续期失败被抢占/1=事件线程死（on_fatal 告警后）/0=正常收尾
- 订阅真相源（**四源**）：`live_task(running).symbol ∪ system_config.hub_shadow_symbols ∪ minute_history_start 池成员 ∪ hub_transient_subs(30min TTL 临时)`；读失败沿用旧集。diff 增删（先加后退）/全量幂等重放（**先退 removed** 防订阅泄漏）/重连沿强放/退订前 flush_symbol——语义收编 `runtime.subs.SubscriptionManager`（纯逻辑不持周期，节奏由钩子注册）
- 落库：`bar_hub` 表（_PGWriter 独立线程批量，ON CONFLICT 幂等）
- 心跳：见下方字段表。tick 断流 300s（时段+已有 tick 基线）**只告警（文案带 runbook），不自杀**（S6 修订）——L2 段收编 `runtime.mdlink.MdSessionSupervisor`（定时续航/反应式重登/恢复/双通道限频告警）
- 启动时 health_monitor schema 校验（入口路由，不阻断）；MD 生命周期日志走 EVENT_LOG 可观测（journalctl 滤 `[gw]`）

## runtime 骨架依赖（批 2 迁移后主循环形态）

```
EngineLoop(loop.py, step=5s) ──到期驱动──
 ├─ preflight（内建）：watchdog 喂狗 + EventEngine 线程存活（死→on_fatal 告警+exit 1）
 ├─ heartbeat ────────────▶ HeartbeatWriter(pulse) ─▶ Valkey
 ├─ l2-supervise（每步）──▶ MdSessionSupervisor(mdlink)
 │                           ├─ SessionCounters(pulse)：时段沿/基线/zombie/stalled
 │                           ├─ XtpMdSession(md_session)：定时续航+反应式重登
 │                           └─ AlertPolicy(alerts)：600/300/150/30/60（=hub 现值）
 ├─ subs-poll / subs-replay / md-edge ▶ SubscriptionManager(subs) ─▶ GuardedXtpMdApi(md_api_guard)
 └─ lease-renew / flush ───▶ parts（租约 Lua / MinuteAggregator 双 flush 窗口）
横切：make_alert / make_guard / make_valkey（alerts）三件套；quant_common.session 时段判定
```

关键签名一行各：`EngineLoop.every(name, period, fn, failure="log")` · `SessionCounters.on_data(in_session) / apply_edge(in_session)->bool / zombie(now, trading_day, grace) / stalled(now)` · `HeartbeatWriter.beat(**extra)` · `MdSessionSupervisor.tick(in_session, trading_day)`（永不抛）· `SubscriptionManager.poll() / replay() / on_reconnect_edge()` · `AlertPolicy + make_alert / make_guard / make_valkey`。

## EngineLoop 钩子表（main.py 注册；period=0=每步）

| 钩子 | period | 语义 |
|---|---|---|
| `lease-renew` | 5s | 租约 Lua CAS 续期（失败 exit 5 在钩子内自带；网络异常容忍一轮） |
| `md-edge` | 每步 | MD 重连沿 → 强制全量重放（XTP 重连不恢复订阅） |
| `subs-poll` | 15s | 订阅 diff（旧 counter%3） |
| `subs-replay` | 60s | 全量幂等重放（旧 %60<10 窗口法——差异见下） |
| `flush` | 5s | 双 flush 窗口判断（11:30:05/15:00:05，逻辑原样） |
| `heartbeat` | 5s | 心跳写（R-OBS1） |
| `l2-supervise` | 每步 | L2 五段（沿→续航→反应式→恢复→例行告警）；交易日查询=md_session.is_trading_day 按日缓存（D2 下沉） |
| （内建 preflight） | 每步 | 喂狗 + 事件线程存活（R-BR12，死→exit 1） |

## 心跳字段（`quant:hb:md-hub`，TTL 90s，8 字段+ts）

| 字段 | 义 |
|---|---|
| `pid` / `gen` | 进程号 / 代次（INCR 永不回退，消费方 fencing 依据） |
| `subs` | 当前已同步订阅数 |
| `ticks` / `bars` | 进程累计 tick / bar 计数 |
| `sess_ticks` | 时段作用域 tick 基线（沿上清零，S6；/metrics 有对应 counter） |
| `last_tick_ts` | 最近 tick 墙钟（进程累计） |
| `dropped_pg` | 落库缓冲溢出丢弃计数 |
| `ts` | HeartbeatWriter 兜底时间戳（批 2 新增） |

超集原则：旧字段名一字不改只增（消费方 `health_monitor/collector.py` 字段清单双向锁进测试）。

## 行为差异（批 2 迁移确证，知情接受——完整老/新映射表见 `docs/任务/批2-runtime骨架与hub首迁.md`）

- 重放 `%60<10` 相位窗 → 60s 确定性周期（**修复**而非等值——旧法 1/3 分钟可能错过整窗）
- zombie 判定新增 trading_day 门（有益）；告警节奏锚从进程相位改症状起点（首报恒 +period，更冷静）
- sess_ticks 出沿清零（夜间心跳为 0，纯观测）；收盘后不再空调 on_recovered；启动 t0 多一次幂等重放
- 其余（租约续期/flush/心跳键/订阅/L2 阈值退避/parts 数据面）逐字等值——双盲审机械对照 + parts.py 字节级 diff 证实

## 依赖

vnpy（EventEngine/MdApi）· Valkey · PG（bar_hub/system_config/live_task/pools/hub_transient_subs）· strategy_framework（runtime 五模块 / md_api_guard / md_session / broker.build_xtp_setting）· quant_common（session/guard——2026-08-19 归位直连，原寄生 strategy_runner 及连带 vnpy 链已解）· alert_notify（经 runtime.alerts）

## 被调

无（终端进程）。worker（strategy_runner/hub_worker）消费其流。

## 读写表

bar_hub（写）· system_config（读）· live_task（读）· pools+pool_symbols（读，池源）· hub_transient_subs（读 + DELETE 过期行）

## 最近变更

- 2026-08-27（批 4c）：批 2 挂账清偿——parts.py 拆分说明 / runtime 依赖图+签名 / EngineLoop 钩子表 / 心跳 8 字段+ts 表（旧文档 6 字段清单本就滞后）/ 行为差异段回写（由头：批 2 双盲审 P2 落档「md_hub.md 模块契约回写欠账」）；D2 交易日按日缓存注记
- 2026-08-25（批 2）：主循环迁 runtime 骨架（行为值不变）；数据面部件移驻 parts.py；心跳增 ts
- 2026-08-20：订阅生命周期闭环（双向 diff 退订+重放窗口退订）；三档项 12 latest_tick
- 2026-08-18（S6 修订）：tick 断流自杀已删只告警；心跳新增 sess_ticks；启动 schema 校验
- 2026-08-17：初版（ST7）
