# 模块契约 · md_hub（共享行情 Hub，ST7 2026-08-17）

> 终态设计：`docs/design/D26-市场接入架构.md`（账号级拓扑——本契约记录现行代码形态=单活双实例，D26 实施批切换后更新）。纯数据面（无下单/无风控）。
> 批 2（2026-08-25）主循环迁上 `strategy_framework/runtime/` 骨架：EngineLoop 到期驱动钩子（废 counter%N 相位耦合），L2 会话自愈收编 MdSessionSupervisor——行为值不变（确证差异见「行为差异」节）。

> **最近变更（2026-09-24 D6 + 加密接入批）**：键 account 化 `_key(base, account_id)`（租约/心跳/active_instance 加 account 后缀）；会话 `_in_bar_session(t, market)` 加密 24/7 + `flush_stale`；流键发布侧 crypto 走 `hub:bars:{account_id}:{symbol}` + account_id 字段。
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
| `main()` | 入口 `python -m src.md_hub.main` | systemd `quant-md-hub@quant`（A 现任）/ `@quant2`（B 继任，按需启动）——**单活双实例**（M5 批 60：active_instance 仲裁，任一时刻至多一个写者） |
| `_boot_dispatch(r, instance_name)` | `-> (uuid, gen)` | M5 boot 单判定+dispatch 分叉（插 `_lease_boot` 前）：intent.target==自己→guarded / intent.target≠自己→exit(6) / intent 无且 active_instance≠自己→exit(6) / 否则→normal 冷启 |
| `_read_intent(r)` | `-> {snapshot,target}\|None` | 读 `hub:switch:intent`（dict+字段类型校验，坏值返 None） |
| `_intent_poll()` | EngineLoop 钩子 5s | M5：见 intent 且 target≠自己 → CAS DEL lease → exit(0) 优雅让位 |
| `LATEST_TICK_PREFIX` | 重导出 | test_stock_detail 经 main 取用 |

**switch.py（M5 切换编排器，批 60）**

| 符号 | 说明 |
|---|---|
| `main()` 子命令 | `confirm <target>`（阻塞式：闸→target 校验→SET intent→等让位→quant-svc start 目标→等接管+心跳 gen 证据→校验 active_instance→DEL intent→审计）/ `check` / `abort` / `diff <date>`（首日 bar_hub vs bar_1min 全量口径比对） |
| 退出码 | 0=成功 / 2=有 running 任务 / 3=等让位超时 / 4=等接管超时 / 5=target 非法；SIGHUP 忽略（confirm 阻塞驻留防关终端杀，下沉 main()） |

**parts.py（数据面部件）**

| 符号 | 签名 | 说明 |
|---|---|---|
| `MinuteAggregator` | `on_tick(symbol, tick) -> dict\|None`；`flush_minute(minute_slot) -> list[dict]`；`flush_symbol(symbol) -> dict\|None` | 分钟聚合（分钟末标注/累计差分含冷启动基线/跨日清零/untrusted 双门限+收盘桶豁免 11:29/14:59/15:00）；flush_minute=三窗分窗 finalize（pop 幂等，P2 修复批 08-28 替代 flush_all）；flush_symbol=退订前防丢在桶最后一分钟 |
| `_PGWriter` | `push(bar)`；daemon 线程 | bar 批量落库（10s 批/有界队列 5000 溢出丢最旧，不反压分发） |
| `_lease_boot(r, instance_name="")` / `_lease_acquire(r, instance_name="")` | `-> (uuid, gen)` | 租约启动（先拿权再连行情）：3 次重试；真让位 SystemExit(3)，耗尽 os._exit(4)；区分存储不可达与 NX 失败；uuid 运行时 token_hex；normal 冷启 SET active_instance（M5 bootstrap） |
| `_lease_acquire_guarded(r, expected_gen, target, uuid_)` | `-> (ok, uuid, gen)` | M5 切换目标接管原子 Lua（首接 INCR+抢 lease+SET active_instance / 重启只抢 lease / 污染拒零污染，校验在 INCR 前）；-2=旧 lease 挡（3 次重试）/0=存储不可达 |
| `_lease_release(r, uuid_)` | `-> bool` | M5 让位 CAS DEL lease（值==my_uuid 才删） |
| `_write_latest_tick(r, symbol, tick, fail_ts)` | | 最新 tick 快照（0 价过滤前置/连败 60s 退避防半死 Valkey 拖死主链） |
| `ThinGateway` | BaseGateway 子类 | 仅事件转发；connect/send_order 等抽象方法全 stub（数据面禁交易 R-HALT1 代码级保证） |
| `_project_symbol(tick)` | TickData→`600000.SHSE` | vnpy SSE→项目 SHSE |
| `_in_bar_session(t)` | `datetime -> bool` | 聚合喂入门（P2 修复批 08-28）：`930<=hm<1130 or 1300<=hm<1501`——盘前/午休尾/收盘后快照不进聚合，冷启动基线顺延至 09:30 后首笔（与 vnpy 首笔建基线一致化）；仅拦 agg 喂入，latest_tick/心跳不受影响 |
| `LEASE_KEY`/`GEN_KEY`/`SURRENDER_KEY`/`INTENT_KEY`/`ACTIVE_INSTANCE_KEY`/`_LEASE_RENEW_LUA`/`_GUARDED_ACQUIRE_LUA`/`_CAS_DEL_LUA` | 常量 | 租约三键 + M5 切换两键（intent{snapshot,target} EX300 / active_instance 无 TTL）+ Lua 脚本三份（CAS 续期/guarded 条件推进/CAS DEL） |

## 行为契约

- 分发：`XADD hub:bars:{symbol}` MAXLEN~5000，字段 `gen/seq/ts/pub_ts/untrusted/ohlc/volume/amount/tick_count`；seq 成功后才占号（失败不留洞）；事件线程 on_tick 与主循环 flush 经 seqs_lock 互斥
- 最新 tick：`SET hub:latest_tick:{symbol}` TTL 65s（三档项 12）——价量+五档+涨跌停，每 tick 写；断流 65s 自动过期（消费方 `stock_detail._quote_block` 降级腾讯源）
- fencing：租约 `hub:lease`（SET NX EX30 + Lua CAS 续期 5s 一续）；`gen = INCR hub:gen` 永不回退。**退出码矩阵（M5 批 60 扩）**：0=优雅让位（见 intent 主动让位）/1=事件线程死·guarded 污染拒（重启码）/3=真让位（NX 失败他人持有；**重启码**——含正常重启 30s lease 滞后自愈，切换窗拦截由 6 承接）/4=启动重试耗尽（重启码）/5=续期失败被抢占（重启码）/6=boot 闸拦截（被切走者·非现任）/78=B 凭证取数失败 fail-fast（禁 .env fallback）；unit `RestartPreventExitStatus=0 6 78`
- M5 切换协议（批 60 v15）：boot 单判定（intent.target 放行目标/拦截被切走者 + active_instance 兜切换后重启仲裁）；guarded Lua 三态原子（正切 B/反切 A 通用）；holder 校验=active_instance==target（等强度代理）；worker 侧 gen 跳变重暖机复用（14 号现状）
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
 └─ lease-renew / flush ───▶ parts（租约 Lua / MinuteAggregator 三窗分窗 finalize）
横切：make_alert / make_guard / make_valkey（alerts）三件套；quant_common.session 时段判定
```

关键签名一行各：`EngineLoop.every(name, period, fn, failure="log")` · `SessionCounters.on_data(in_session) / apply_edge(in_session)->bool / zombie(now, trading_day, grace) / stalled(now)` · `HeartbeatWriter.beat(**extra)` · `MdSessionSupervisor.tick(in_session, trading_day)`（永不抛）· `SubscriptionManager.poll() / replay() / on_reconnect_edge()` · `AlertPolicy + make_alert / make_guard / make_valkey`。

## EngineLoop 钩子表（main.py 注册；period=0=每步）

| 钩子 | period | 语义 |
|---|---|---|
| `lease-renew` | 5s | 租约 Lua CAS 续期（失败 exit 5 在钩子内自带；网络异常容忍一轮；M5 前置查 intent——target≠自己才停续租，target==自己仍续租防泄漏 exit 5） |
| `intent-poll` | 5s | M5：轮询切换意图（见 intent 且 target≠自己 → CAS DEL lease → exit(0) 优雅让位） |
| `md-edge` | 每步 | MD 重连沿 → 强制全量重放（XTP 重连不恢复订阅） |
| `subs-poll` | 15s | 订阅 diff（旧 counter%3） |
| `subs-replay` | 60s | 全量幂等重放（旧 %60<10 窗口法——差异见下） |
| `flush` | 5s | 三窗分窗 finalize（11:30 窗收 11:29 桶/15:00 窗收 14:59 桶/15:01 窗收 15:00 桶；宽窗 5~30s，pop 幂等——P2 修复批 08-28，双轨四分类③） |
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

## 行为差异（批 2 迁移确证，知情接受——完整老/新映射表见 `docs/obsolete/任务归档/批2-runtime骨架与hub首迁.md`）

- 重放 `%60<10` 相位窗 → 60s 确定性周期（**修复**而非等值——旧法 1/3 分钟可能错过整窗）
- zombie 判定新增 trading_day 门（有益）；告警节奏锚从进程相位改症状起点（首报恒 +period，更冷静）
- sess_ticks 出沿清零（夜间心跳为 0，纯观测）；收盘后不再空调 on_recovered；启动 t0 多一次幂等重放
- 其余（租约续期/flush/心跳键/订阅/L2 阈值退避/parts 数据面）逐字等值——双盲审机械对照 + parts.py 字节级 diff 证实

## 依赖

vnpy（EventEngine/MdApi）· Valkey · PG（bar_hub/system_config/live_task/pools/hub_transient_subs）· strategy_framework（runtime 五模块 / md_api_guard / md_session / broker.build_xtp_setting）· quant_common（session/guard——2026-08-19 归位直连，原寄生 strategy_runner 及连带 vnpy 链已解）· alert_notify（经 runtime.alerts）

## 被调

无（终端进程）。worker（strategy_runner/hub_worker）消费其流。

## 读写表

bar_hub（写）· system_config（读）· live_task（读）· pools+pool_symbols（读，池源）· hub_transient_subs（读 + DELETE 过期行）· external_interface（读，M5 B 实例按 HUB_INTERFACE_ROW row_id 取凭证）· audit_log（写，switch.py 切换审计）

## 最近变更

- 2026-08-27（批 4c）：批 2 挂账清偿——parts.py 拆分说明 / runtime 依赖图+签名 / EngineLoop 钩子表 / 心跳 8 字段+ts 表（旧文档 6 字段清单本就滞后）/ 行为差异段回写（由头：批 2 双盲审 P2 落档「md_hub.md 模块契约回写欠账」）；D2 交易日按日缓存注记
- 2026-08-25（批 2）：主循环迁 runtime 骨架（行为值不变）；数据面部件移驻 parts.py；心跳增 ts
- 2026-08-20：订阅生命周期闭环（双向 diff 退订+重放窗口退订）；三档项 12 latest_tick
- 2026-08-18（S6 修订）：tick 断流自杀已删只告警；心跳新增 sess_ticks；启动 schema 校验
- 2026-08-17：初版（ST7）
