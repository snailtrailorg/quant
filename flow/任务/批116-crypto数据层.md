# 批 116 · crypto 数据层（剩余范围）

> 立项 2026-10-09（主会话补决策后立项）。**2026-10-09 C 组步 2 双盲审 P0-1/P0-2 返工重写**：原版按 10-05 研究稿把批 101/101b/102b 已落地项当待做（83b 教训二犯），且引用了不存在的 `Temporality.continuous` 枚举。本版按库/真机实测重写现状与剩余范围。
> 关联：批 101（币安日线）/ 101b（币安盘中）/ 102b（OKX 日线，五证上产 `202610061532-f5d0e55`）/ `flow/decisions.md` 2026-10-09 决策 B（**已修正**：continuous 子句撤回）

## 目标

crypto 数据层的**剩余缺口**收口：① okx_perp_daily 10-08/10-09 晨档缺跑排查（新发现，见现状 #5）；② OKX 盘中粒度（hourly/1min/15min，如需）；③ crypto 时间轴表达重裁（`streaming` vs 契约层扩枚举——设计变更须设计先行）。

## 现状（已实证 · 2026-10-09 对库/真机核实）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | 币安 + OKX **数据源层已完成**：adapter（`binance_adapter.py`/`okx_adapter.py`）、三注册表、接口层（`interfaces/binance_perp.py`/`okx_perp.py`） | `server/src/data_platform/adapters/`、`interfaces/` |
| 2 | 5 个 crypto 同步项已配置且 enabled：`binance_perp_{daily,hourly,1min,15min}` + `okx_perp_daily`；`start_floor` 已回填（daily 2019-09-08 / okx 2020-01-01 / hourly 2026-09-29 / 分钟 2026-10-05） | dev 库 `sync_config` 实查；批 102b 迁移 0137 五证上产 |
| 3 | 死构件 `data_increment_crypto` 已随批 101 退役 | `scheduler/app.py:154` 仅存注释；`tasks.py` grep 零命中 |
| 4 | prod 运行证据：`binance_perp_daily` 今晨 08:51 成功（1004 标的，拉/存 914 行，失败 0，游标 20261007）——08:38 僵尸复位（1423 min 未更新＝10-08 08:55 起挂）后重跑成功 | prod worker journal 2026-10-09 |
| 5 | `okx_perp_daily` 最近可见成功＝**10-07 08:44**（485 标的全存，排除段登记 2 条落 sync_gap）；10-08/10-09 晨档**无执行痕迹**（无成功行、无僵尸复位行；今晚调度器显示其「未到周期」⇒ next_run 已滚至 10-10）。**复现**：`quant-journal -u quant-celery-worker@quant.service --since -72h -n 300000 | grep okx_perp_daily`（ansible ad-hoc）；或查 `sync_log` `id='okx_perp_daily'` 最新行 | prod worker journal -72h（2026-10-09 20:29 取证） |
| 6 | OKX **盘中粒度未做**：`okx_perp_hourly/1min/15min` 全仓 grep 零命中 | grep `server/src/` |
| 7 | `Temporality` 无 `continuous` 枚举（仅 historical/snapshot/streaming）——决策 B 该子句已撤回 | `quant_common/contract.py:44` |
| 8 | OKX 源重试预算偏薄（102b 挂账：7/485 标的 3 次退避后仍 ConnectionReset） | `flow/任务/批102b-OKX数据层.md` 待办注记 |

## 步 0 排查结论（2026-10-09 深夜落账）

**A. okx_perp_daily 10-08/09 晨档缺跑——根因定案：OKX 代理出口不通（infra）**
- 证据链：① 调度器两晨档**确有触发**（10-08 08:43:57 / 10-09 08:43:59 `n_triggered=2` 含 okx，内联跑 16-18s）；② 无 handler 完成行 ⇒ `list_symbols()` 阻塞 ~16s 后 `raise RuntimeError`（`engine.py:1307`，enum_hint 自述「instruments 接口不可达（含代理出口未配/不通）」）；③ `sync()` except（`:470`）落 `sync_log` error 行 + 告警 + `last_sync_ts` 刷新（⇒ 调度器下轮「未到周期」，无重试风暴——机制按设计工作）；④ **prod 直连 OKX 实测 `curl rc=124` 超时**（OKX 大陆被墙，批 102a 设计须走代理出口；代理出口 10-08 起不通）。
- **数据面**：游标停 20261006，10-07 起缺数据（游标契约会自动补——代理恢复后下一轮从 10-07 重拉，`cursor_upto`＝实际取到数据的最后一日）。
- **处置**：代理出口修复属 **infra/装位**（同批 111 infra 两则，交办 michael/用户）；代码面**无需修**——失败可见性（sync_log error + 告警）按设计工作。附产品缺陷：调度器 16s 内联阻塞（okx 不在 `_SYNC_ASYNC_DISPATCH` 异步清单）——**顺手改进候选**：把 crypto 日线档加进 `_SYNC_ASYNC_DISPATCH` 防调度轮长阻塞（beat 5min 一轮被占 16s+）。
- 待办：代理恢复后验证一轮自动补拉（游标 20261006 → 追平）。

**B. 其余步 0 项**（时间轴重裁 / OKX 盘中需求确认）：未做，随批推进。

## 依赖（就绪）

批 101/101b/102b 全部 ✅（上产）；`sync_gap` 排除段机制 ✅（批 109）；102a 代理体系 ✅（**但出口当前不通**，见步 0-A——infra 修复前置）。

## 引用（设计 / 方案）

- `flow/方案/多市场数据接入与代理体系-设计-20261006.md` §二/§四 —— crypto 接入设计真源。
- `flow/decisions.md` 2026-10-09 决策 B（修正版）—— start_floor=NULL 保留窗口、键 (symbol) 仍有效；时间轴表达待重裁。

## 产出

1. **步 0 · okx_perp_daily 缺跑排查（最优先，数据完整性）**：查 10-08/10-09 晨档为何未触发/无痕（对照 binance 僵尸复位路径——okx 是否也挂了但复位行丢失？`sync_log`/`sync_gap` 落库核对），补拉缺口（手动触发 `sync("okx_perp_daily")` 或等 10-10 晨档并验证），根因若在调度器/zombie-reset 则修。
2. **OKX 盘中粒度**（hourly/1min/15min，沿用已泛化的工厂范式 `_make_crypto_bar_handler`（`engine.py:1254`，币安四项均经它生成）加 OKX 规格）——**须先确认需求**（威廉姆 101b 裁定「功能验证优先」时未承诺 OKX 盘中；若不需要则从本批范围移除并记待办）。
3. **时间轴表达重裁（设计变更，设计先行）**：crypto 日线现走 `bar_daily` kind（trade_date 语义）实际是 24/7 连续流——重裁「用 `streaming` 语义 re-label vs 扩 `Temporality` 枚举 vs 维持现状（bar_daily + trade_day_filter=none 已工作）」。**若结论是「维持现状」则零代码**，只把决策写进设计文档。
4. OKX 重试预算（102b 挂账，随批顺带：退避 3→5 次或加 ConnectionReset 专项重试）。

## 限定范围

只改 `okx_adapter.py`/`engine.py`（若做盘中粒度）、调度器 zombie-reset（若步 0 根因在此）、设计文档（时间轴重裁）。
**不碰**：币安侧已稳定路径、批 109 `sync_gap` 机制、A 股同步。

## 接口契约

- （步 0 排查产出：缺跑根因 + 补拉凭证 `sync_log` 行；若修调度器给出 diff）
- （盘中粒度若做：`okx_perp_hourly/1min/15min` 三个 sync_config 行 + handler，契约同 101b 范式）

## 验收标准

- 步 0：okx_perp_daily 缺口补拉成功（`sync_log` 最新 success 行，游标追平今日）+ 根因结论落任务文件
- `cd server && ./venv/bin/python -m pytest tests/ -q` → 全绿（若改代码）
- 盘中粒度若做：对照 101b 验收（mock 单测 + 真机尾段）
- 时间轴重裁：决策落 `docs/design/` 对应文档并引用回本文件

## mock 方式

同 101b/102b（adapter mock 返回可辨识假数据；不连真 OKX——dev 出不了网，真机留 prod 实测）。
