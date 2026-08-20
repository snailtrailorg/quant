# 模块契约 · md_hub（共享行情 Hub，ST7 2026-08-17）

> 设计：`docs/architecture/14-共享行情hub设计.md` v2；需求：13 号。纯数据面（无下单/无风控）。

## Public API

| 符号 | 签名 | 说明 |
|---|---|---|
| `MinuteAggregator` | `on_tick(symbol, tick) -> dict\|None`；`flush_all() -> list[dict]` | 分钟聚合（分钟末标注/累计差分含冷启动基线/跨日清零/untrusted 双门限+收盘桶豁免） |
| `main()` | 入口 `python -m src.md_hub.main` | systemd `quant-md-hub@quant` 单实例 |
| `_project_symbol(tick)` | TickData→`600000.SHSE` 项目口径 | vnpy SSE→项目 SHSE |

## 行为契约

- 分发：`XADD hub:bars:{symbol}` MAXLEN~5000，字段 `gen/seq/ts/pub_ts/untrusted/ohlc/volume/amount/tick_count`
- 最新 tick：`SET hub:latest_tick:{symbol}` TTL 65s（三档项 12，2026-08-20）——价量+五档+涨跌停，每 tick 写；断流 65s 自动过期（消费方 `stock_detail._quote_block` 降级腾讯源）
- fencing：租约 `hub:lease`（SET NX EX30 + Lua CAS 续期）；`gen = INCR hub:gen` 永不回退；被抢占让位 exit(3)
- 订阅真相源：`live_task(running).symbol ∪ system_config.hub_shadow_symbols`，30s diff + 60s 幂等重放
- 落库：`bar_hub` 表（独立线程批量，ON CONFLICT 幂等，有界队列不反压分发）
- 心跳：`quant:hb:md-hub`（pid/gen/subs/ticks/bars/last_tick_ts，TTL 90s）；tick 断流 300s（时段+已有tick）自杀重启
- 复用 strategy_runner 的 `_guard/_sd_notify/_alert/_in_astock_session`（SA 机制）

## 依赖

vnpy(EventEngine/MdApi) · Valkey · PG(bar_hub/system_config/live_task) · broker_config 凭证（PI3）

## 被调

无（终端进程）。worker（hub_worker）消费其流。

## 读写表

bar_hub（写）· system_config（读）· live_task（读）


## 增量（2026-08-18 S6 修订）
- tick 断流自杀已删：只告警（文案带 runbook）；基线=时段作用域（`sess_ticks`/`sess_last_tick`，进入沿清零）
- 心跳新增 `sess_ticks` 字段（/metrics 有对应 counter）
- 启动时 health_monitor schema 校验（入口路由）

- 共享工具直连 quant_common（原模块级 import strategy_runner——连带 vnpy 链已解）
