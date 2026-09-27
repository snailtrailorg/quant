# 模块契约 · md_hub（共享行情 Hub，ST7 2026-08-17）

> 终态设计：`docs/design/D26-市场接入架构.md`（**批 66a/66b/66c 已实施上产**——本契约现行代码形态=账号级 hub：unit `quant-md-hub@{account_id}`，一行接口=一 hub）。纯数据面（无下单/无风控）。
> 批 2（2026-08-25）主循环迁上 `strategy_framework/runtime/` 骨架：EngineLoop 到期驱动钩子（废 counter%N 相位耦合），L2 会话自愈收编 MdSessionSupervisor——行为值不变（确证差异见「行为差异」节）。

> **最近变更（批 66a/66b/66c，2026-09-26/27 上产）**：M5 机关全退役（switch.py 删/退出码 5/6 退役/Prevent=0 78）+ boot 守卫 a′（SET NX EX 30 per-account）；unit=`quant-md-hub@{account_id}`（数字实例名=接口行 id，`@quant` 过渡实例已 disable）+ ACCOUNT_ID 必数字断言（非数字 exit 78）；流键/心跳/latest_tick 全键族 per-account（`_key`）；payload 恒带 account_id；`_poll_iface_switch` 扩凭证摘要（三元组）+读行失败版本顺延；STREAM_MAXLEN 从 MarketSpec channels 读（`_stream_maxlen`）；时段谓词 `in_session(market)`。
## 文件结构

```
server/src/md_hub/
├── main.py    # 入口 + 钩子编排（批 2 重写；批 66a 删 M5 机关）：租约/守卫接线/EngineLoop 注册/事件 handler/iface 对账
└── parts.py   # 数据面部件：聚合器/租约（a′ per-account）/latest_tick（批 66a 删 M5 Lua×2；批 63 二 ThinGateway 移驻 md_gateway；PG 写 2026-09-23 退役）
```

parts.py 的公共件在 main.py 顶部 import 重导出——既有测试导入路径（test_hub_arch/test_stock_detail）不受影响（ThinGateway 批 63 二起不再经 main 重导出）。

## Public API

**main.py**

| 符号 | 签名 | 说明 |
|---|---|---|
| `main()` | 入口 `python -m src.md_hub.main` | systemd `quant-md-hub@{account_id}`（批 66b：**实例名=接口行 id（数字）**，unit 注入 `INSTANCE_NAME/ACCOUNT_ID=%i`；`@quant` 过渡实例已随 release 波次 disable——以本代码启动即 78）。启动断言 ACCOUNT_ID 必数字（非数字 exit 78）；接口行 row_id 解析：HUB_INTERFACE_ROW 过渡优先（@quant concrete unit 注入）→ 否则 ACCOUNT_ID → 皆空 EX_CONFIG(78)；网关插件 `create_md_gateway(provider)`（批 63 二：接口行 provider 选插件，未注册→78） |
| `_stream_maxlen(market)` | `-> int` | 批 66c：STREAM_MAXLEN 缺省从 `MARKETS[market].channels.bar.stream_maxlen` 读（消声明孤岛），缺省 5000（≈20 交易日分钟 bar） |
| `_cred_hash(credentials)` | `-> str` | 批 66a：凭证摘要（解密后明文 canonical json 的 sha256）——凭证轮换触发 exit 9 的比对基；params 变更不触发 |
| `_poll_iface_switch(r, prev_version, row_id, current_provider, prev_cred_hash=None)` | `-> (新版本, 新凭证摘要, 是否切换)` | 批 64+66a：对账 external_interface 行——provider **或凭证摘要**变化即切换（66a 闭「只改凭证不 bump 版本漏检」缺口）。prev_version=None=首次读基线（版本与摘要同轮建立）；Valkey 读失败返回原值不判切换；读行失败**版本顺延**（固化新版本+旧摘要会漏掉本轮即轮换——盲审 A-P1）。switched 时 main 消费方 `os._exit(9)` 让 systemd 拉起重读行 |
| `LATEST_TICK_PREFIX` | 重导出 | test_stock_detail 经 main 取用 |

**switch.py——已删除（批 66a）**：M5 切换编排器（confirm/check/abort/diff 子命令、intent/active_instance/guarded Lua 协议）随 M5 退役整体删除（D26 §4.1——互备体系让位于账号级拓扑：一行一 hub，切换=Web 拖拽行 → cfg:version 对账 exit 9 → systemd 重启读新行，`_poll_iface_switch` 承接）。

**parts.py（数据面部件）**

| 符号 | 签名 | 说明 |
|---|---|---|
| `_key(base, account_id)` | `-> str` | 键 account 化（D6/批 66b）：`account_id is None` 返 base（防御分支——66b 起 main 已断言恒数字，实际不再出现 None）；否则 `{base}:{account_id}`。租约/gen/心跳/latest_tick 全经此分叉 |
| `MinuteAggregator` | `on_tick(symbol, tick) -> dict\|None`；`flush_minute(minute_slot) -> list[dict]`；`flush_symbol(symbol) -> dict\|None`；`flush_rest() -> list[dict]`；`flush_stale(now) -> list[dict]` | 分钟聚合（分钟末标注/累计差分含冷启动基线/跨日清零/untrusted 双门限+收盘桶豁免 11:29/14:59/15:00）；flush_minute=三窗分窗 finalize（pop 幂等，P2 修复批 08-28 替代 flush_all）；flush_symbol=退订前防丢在桶最后一分钟；flush_rest=日终兜底收滞留桶（15:01 窗后防次日丢根）；flush_stale=加密 24/7 收陈旧桶（只收分钟已过去的桶，不碰在途桶——盲审 A/B P0） |
| ~~`_PGWriter`~~ | — | **已删除（2026-09-23 迁移 0096，退役自攒历史分钟线）**：bar_hub 落库链整体退役（表已清空/删表），hub 只做实时 Valkey 分发；心跳 `dropped_pg` 字段保留恒 0（超集原则） |
| `_lease_acquire(r, account_id=None)` | `-> (ok, uuid, gen)` | 批 66a a′ 守卫：SET NX EX 30（键经 `_key`）+ 通过后 INCR gen；-1=真让位（surrender）/0=存储不可达重试；M5 active_instance 仲裁与 guarded Lua 已退役——带外第二进程 NX 恒失败=同账号双活被防，长活 fencing 成立 |
| `_lease_boot(r, account_id=None)` | `-> (uuid, gen)` | 租约启动（先拿权再连行情）：3 次重试；真让位 SystemExit(3)，耗尽 os._exit(4)；真让位写 surrender 标记（EX 600） |
| `_write_latest_tick(r, symbol, tick, fail_ts, account_id=None)` | | 最新 tick 快照 per-account 键（`_key(LATEST_TICK_PREFIX, account_id)+:{symbol}`；0 价过滤前置/连败 60s 退避防半死 Valkey 拖死主链） |
| `ThinGateway` | — | 批 63 二移驻 `strategy_framework/md_gateway.py`（层序：strategy_framework 禁 import 上层；vnpy 网关薄壳归 vnpy 集成层） |
| `_project_symbol(tick)` | TickData→`600000.SHSE` | vnpy SSE→项目 SHSE |
| `_in_bar_session(t, market="astock")` | `datetime, str -> bool` | 聚合喂入门（P2 修复批 08-28）：astock `930<=hm<1130 or 1300<=hm<1501`；`market="crypto"` 恒 True（24/7）——盘前/午休尾/收盘后快照不进聚合，冷启动基线顺延至 09:30 后首笔；仅拦 agg 喂入，latest_tick/心跳不受影响 |
| `LEASE_KEY`/`GEN_KEY`/`SURRENDER_KEY`/`LATEST_TICK_PREFIX`/`LATEST_TICK_TTL`/`_LEASE_RENEW_LUA` | 常量 | 租约两键+surrender 标记+latest_tick 前缀/TTL + CAS 续期 Lua（批 66a：INTENT_KEY/ACTIVE_INSTANCE_KEY/_GUARDED_ACQUIRE_LUA/_CAS_DEL_LUA 随 M5 退役删除） |

## 行为契约

- 分发（批 66b 键模型）：`XADD hub:bars:{account_id}:{symbol}`（启动断言 account_id 恒数字——旧 A 股裸键/条件分支退役），MAXLEN=`_stream_maxlen(market)`（批 66c：MarketSpec `channels.bar.stream_maxlen` 同源，缺省 5000）approximate；字段 `gen/seq/ts/pub_ts/untrusted/open/high/low/close/volume/amount/tick_count` + **`account_id` 恒写**（批 66b 双盲 P0：键统一而 payload 不统一=A 股 bar 被 worker `_account_mismatch` 全拒）；seq 成功后才占号（失败不留洞）；事件线程 on_tick 与主循环 flush 经 seqs_lock 互斥
- 最新 tick：`SET hub:latest_tick:{account_id}:{symbol}` TTL 65s（三档项 12；批 66b per-account）——价量+五档+涨跌停，每 tick 写；断流 65s 自动过期（消费方 `stock_detail._quote_block` SCAN `hub:latest_tick:*:{symbol}` 取 ts 最大者，降级腾讯源）
- fencing（批 66a a′）：boot 守卫=SET NX EX 30 per-account 键（`_key`）+ INCR gen；续租 Lua CAS 5s 一续；`gen = INCR hub:gen:{account_id}` 永不回退。**退出码矩阵（批 66a 起）**：0=优雅退出（冗余防御）/1=事件线程死·EngineLoop fatal·**租约续期 CAS 败**（原 5 随 M5 退役归入此码——继续跑=无 fencing 运行，须退出重抢）/3=boot 守卫真让位（**重启码**——含正常重启 30s lease 滞后自愈：SIGTERM 不 DEL lease，TTL 30s 内新进程 NX 失败 exit(3)，30s 后重拉即愈）/4=启动重试耗尽（Valkey 不可达）/9=接口行 provider/凭证变更自重启（批 64+66a：`_poll_iface_switch` 对账 cfg:version，主动退出让 systemd 拉起重读行，RestartSec=30 即 ~30s 轻量窗载体）/78=EX_CONFIG（ACCOUNT_ID 非数字/网关初始化失败——凭证按 row_id 取数 fail-fast，禁 .env fallback）；unit `RestartPreventExitStatus=0 78`
- ~~M5 切换协议~~ **已退役（批 66a，D26 §4.1）**：switch.py/intent/active_instance/guarded Lua/boot dispatch 全删；退出码 5/6 随之退役；切换语义=Web 拖拽/改接口行 → cfg:version INCR → `_iface_poll` 检出 provider/凭证摘要变化 → exit 9 → systemd 重启读新行。worker 侧 gen 跳变重暖机复用不变
- 网关连接窗（批 66b，D26 §3.3「连接生命周期=hub 内部状态机」）：XTP 沿既有 `xtp_session_window_open` 会话窗（窗关启动 defer_login/窗开沿 relogin）；EMQ（`EmqMdGateway`）`_emq_window_open(now, trading_day)`=交易日 8:25-15:15（非交易日全关——staging 实证周日 Login-1），窗关启动 defer_login（api/spi 已建不登录），`poll_supervise` 窗开沿重登（60s 节流防每步 5s 同步阻塞 Login 循环）；登录失败 fail-fast raise→78 路径（防失聪僵尸占租约）
- 订阅真相源（**三源**，池源已移除 2026-09-04）：`live_task(running).symbol ∪ system_config.hub_shadow_symbols ∪ hub_transient_subs(30min TTL 临时)`；读失败沿用旧集。diff 增删（先加后退）/全量幂等重放（**先退 removed** 防订阅泄漏）/重连沿强放/退订前 flush_symbol——语义收编 `runtime.subs.SubscriptionManager`（纯逻辑不持周期，节奏由钩子注册）
- 落库：~~bar_hub 表~~ **已退役（2026-09-23 迁移 0096）**——hub 不再写 PG，纯实时分发（Valkey 流+latest_tick）；历史分钟=stk_mins 接入位/tencent 分时
- 心跳：见下方字段表（键 per-account）。tick 断流 300s（时段+已有 tick 基线）**只告警（文案带 runbook），不自杀**（S6 修订）——监督器批 63 二收编网关插件内（`md_gw.poll_supervise`）
- 启动时 health_monitor schema 校验（入口路由，不阻断）；MD 生命周期日志走 EVENT_LOG 可观测（journalctl 滤 `[gw]`）

## runtime 骨架依赖（批 2 迁移后主循环形态；批 63 二起监督面=网关插件）

```
EngineLoop(loop.py, step=5s) ──到期驱动──
 ├─ preflight（内建）：watchdog 喂狗 + 事件线程存活（死→on_fatal 告警+exit 1；event_engines=md_gw 供）
 ├─ heartbeat ────────────▶ HeartbeatWriter(pulse) ─▶ Valkey（键 per-account）
 ├─ md-supervise（每步）──▶ md_gw.poll_supervise(in_session=in_session(market), trading_day)
 │                           （网关插件内收编：XTP=XtpMdSession L2 五段续航/重登；EMQ=窗开沿重登）
 ├─ subs-poll / subs-replay / md-edge ▶ SubscriptionManager(subs) ─▶ md_gw.subscribe/unsubscribe
 ├─ iface-poll ───────────▶ _poll_iface_switch（批 64+66a：provider/凭证摘要对账→exit 9）
 └─ lease-renew / flush ───▶ parts（租约 Lua per-account / MinuteAggregator 三窗 or flush_stale）
横切：make_alert / make_guard / make_valkey（alerts）三件套；quant_common.session in_session(market)（批 66c per-market）
```

关键签名一行各：`EngineLoop.every(name, period, fn, failure="log")` · `SessionCounters.on_data(in_session) / apply_edge(in_session)->bool / zombie(now, trading_day, grace) / stalled(now)` · `HeartbeatWriter.beat(**extra)` · `SubscriptionManager.poll() / replay() / on_reconnect_edge()` · `create_md_gateway(provider, counters) / md_gw.connect(cred, params) / poll_supervise(in_session, trading_day)`（批 63 二插件面）。

## EngineLoop 钩子表（main.py 注册；period=0=每步）

| 钩子 | period | 语义 |
|---|---|---|
| `lease-renew` | 5s | 租约 Lua CAS 续期（批 66a：失败 exit **1** 在钩子内自带——原 5 随 M5 退役归入通用故障码；网络异常容忍一轮） |
| `md-edge` | 每步 | MD 重连沿 → 强制全量重放（XTP 重连不恢复订阅；connected 由网关插件供） |
| `subs-poll` | 15s | 订阅 diff（旧 counter%3） |
| `subs-replay` | 60s | 全量幂等重放（旧 %60<10 窗口法——差异见下） |
| `flush` | 5s | 三窗分窗 finalize（11:30 窗收 11:29 桶/15:00 窗收 14:59 桶/15:01 窗收 15:00 桶+日终 flush_rest；宽窗 5~30s，pop 幂等——P2 修复批 08-28；批 66c 三窗值挂靠 MarketSpec `flush_policy="three_window"` 声明）；crypto 分支=每 5 分钟 `flush_stale` 收陈旧桶（`flush_policy="stale_5min"`） |
| `heartbeat` | 5s | 心跳写（R-OBS1；键 per-account） |
| `iface-poll` | 15s | 批 64+66a：对账接口行 provider/凭证摘要变更（`_poll_iface_switch`；检出 → `os._exit(9)` 让 systemd 拉起重读行） |
| `md-supervise` | 每步 | 会话监督（批 63 二收编网关插件内——XTP=L2 五段续航/重登/告警；EMQ=窗开沿重登腿）；交易日查询=md_session.is_trading_day 按日缓存（D2 下沉）；`in_session` 实参=`in_session(market)`（批 66c per-market） |
| （内建 preflight） | 每步 | 喂狗 + 事件线程存活（R-BR12，死→exit 1） |

## 心跳字段（`quant:hb:md-hub:{account_id}`（批 66b per-account，经 `_key`），TTL 90s，8 字段+ts）

| 字段 | 义 |
|---|---|
| `pid` / `gen` | 进程号 / 代次（INCR 永不回退，消费方 fencing 依据） |
| `subs` | 当前已同步订阅数 |
| `ticks` / `bars` | 进程累计 tick / bar 计数 |
| `sess_ticks` | 时段作用域 tick 基线（沿上清零，S6；/metrics 有对应 counter） |
| `last_tick_ts` | 最近 tick 墙钟（进程累计） |
| `dropped_pg` | 落库缓冲溢出丢弃计数（bar_hub 落库退役后恒 0，字段保留=超集原则） |
| `ts` | HeartbeatWriter 兜底时间戳（批 2 新增） |

超集原则：旧字段名一字不改只增（消费方 `health_monitor/collector.py` SCAN `quant:hb:md-hub:*` 在场集+legacy 裸键收编 account=0）。

## 行为差异（批 2 迁移确证，知情接受——完整老/新映射表见 `docs/obsolete/任务归档/批2-runtime骨架与hub首迁.md`）

- 重放 `%60<10` 相位窗 → 60s 确定性周期（**修复**而非等值——旧法 1/3 分钟可能错过整窗）
- zombie 判定新增 trading_day 门（有益）；告警节奏锚从进程相位改症状起点（首报恒 +period，更冷静）
- sess_ticks 出沿清零（夜间心跳为 0，纯观测）；收盘后不再空调 on_recovered；启动 t0 多一次幂等重放
- 其余（租约续期/flush/心跳键/订阅/L2 阈值退避/parts 数据面）逐字等值——双盲审机械对照 + parts.py 字节级 diff 证实

## 依赖

vnpy（EventEngine/MdApi，经网关插件）· Valkey · PG（system_config/live_task/hub_transient_subs——bar_hub/pools 已退役）· strategy_framework（runtime 五模块 / md_gateway 网关插件（批 63 二：XtpMdGateway+EmqMdGateway） / md_session.is_trading_day / broker.get_interface_row）· quant_common（session `in_session(market)`（批 66c per-market）/ markets `MARKETS`（66c MarketSpec：stream_maxlen/flush_policy/sessions_skeleton）/ guard）· alert_notify（经 runtime.alerts）

## 被调

无（终端进程）。worker（strategy_runner/hub_worker，`bar_stream_key(symbol, account_id)`）与 DataBus（`subscribe`）消费其流；stock_detail SCAN 其 latest_tick。

## 读写表

system_config（读）· live_task（读）· hub_transient_subs（读 + DELETE 过期行）· external_interface（读，凭证/接口行——row_id 解析 HUB_INTERFACE_ROW 过渡优先→ACCOUNT_ID，批 66a #6 缺省选行已退役）· ~~bar_hub（写，2026-09-23 迁移 0096 退役）~~ · ~~pools+pool_symbols（读，池源 2026-09-04 移除）~~ · ~~audit_log（写，switch.py 已随批 66a 删除）~~

## 最近变更

- 2026-09-27（批 66a/66b/66c）：M5 全退役（switch.py 删/退出码 5/6 退役/Prevent=0 78）+ boot 守卫 a′；unit=`quant-md-hub@{account_id}`+ACCOUNT_ID 必数字断言；流键/心跳/latest_tick/租约 per-account（`_key`）+payload 恒带 account_id；`_poll_iface_switch` 三元组（凭证摘要+版本顺延）；EMQ 连接窗；`_stream_maxlen` MarketSpec 同源；`in_session(market)` per-market
- 2026-08-27（批 4c）：批 2 挂账清偿——parts.py 拆分说明 / runtime 依赖图+签名 / EngineLoop 钩子表 / 心跳 8 字段+ts 表（旧文档 6 字段清单本就滞后）/ 行为差异段回写（由头：批 2 双盲审 P2 落档「md_hub.md 模块契约回写欠账」）；D2 交易日按日缓存注记
- 2026-08-25（批 2）：主循环迁 runtime 骨架（行为值不变）；数据面部件移驻 parts.py；心跳增 ts
- 2026-08-20：订阅生命周期闭环（双向 diff 退订+重放窗口退订）；三档项 12 latest_tick
- 2026-08-18（S6 修订）：tick 断流自杀已删只告警；心跳新增 sess_ticks；启动 schema 校验
- 2026-08-17：初版（ST7）
