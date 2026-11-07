# 批 171d-D · 回补差集（稠密族）+ 预算闸 + 稀疏成功闸 + A 股入对账 scope（D31 批 D）v2

> **依据**（引用 ≤2）：`docs/design/D31-窗口模型统一.md` §4/§3（v2.5 同帧：差集化限稠密白名单/稀疏成功闸覆盖全稀疏轨）；`docs/design/D30-同步缺陷账本.md` DEF-05/07/08。
> **性质**：D31 第三代码批（A=1fbad91/B=2839e8f 已上产）。吸收 171c 腿 2。v2 收编双盲审 A（1P0/7P1/5P2）+ B（3P0/7P1/6P2）——P0 交叉：差集化误罩稀疏族（A）/预算闸单位死锁与返回值三形态（B）/假缺口依赖链（A/B 同撞 400d 窗）。
> **淘汰**：上产后归档。

## 0. 契约（自包含）

- **回补差集化——仅稠密白名单**（A-P0-1）：tier1 工厂回补分支差集化限定 `_DENSE_BACKFILL_IDS = {stk_limit_sync, moneyflow_sync, margin_detail_sync, cyq_perf_sync, st_list_sync}`（每日快照族，有 per-date 期望判据）；**suspend_d/forecast/top_list/block_trade 四稀疏族保持整窗重放**（D31 §4 原文——稀疏表「缺日≠不完整」，差集化会跳过迟到行刷新；重放=upsert 刷新语义）。差集逻辑：回补分支拉取循环前 `_local_dates(table, date_param)` 一次查窗内已有日，`date_range` 过滤为缺失日；**零缺失 ⇒ 跳过拉取直达对账收尾**（pulled=0）。`_local_dates` 返 None（表不可读）⇒ **fail-open 回整段盲拉**+告警（保现状，禁 fail-closed 中止回补——B-P1-3）。
- **via_kind 族段式差集**（A-P2-1/B-P1-1）：daily_batch/cb_daily 是区间单 fetch 非**逐日**循环——差集化按**缺失连续段**聚合（`_fetch_supply(seg_start, seg_end)` 段式调用），禁逐日化（1 次区间调用变 3200 次）。接入表：daily_batch 父级过滤段表传子 handler（加参 `segments` 或预过滤日期集）；cb_daily 同构。
- **per-date 预算闸（stop-after）**（B-P0-1 定稿）：`_reconcile_dates` repull 段加行数累计 `acc += rows; if acc >= budget: break`——**允许末日超限**（原子粒度=全市场单日 ~5566 行，预算 5000<单日 ⇒ A 股族 repull 被闸死是**已知取舍**，DEF-07 早闸见下）；截断 ⇒ 返回体 `budget_truncated: True` + 剩余差集日期回传 handler ⇒ handler 出具 `next_from=min(剩余)`（接 171d-A 链：clamped 出具条件扩 `or budget_truncated`——B-P1-7）。**repull_fn 返回值三形态归一**（B-P0-2，P0 级）：tier1 `_pull_day` 返 int ✓；daily_batch/cb_daily `_pull_day` 返 tuple——**拆元组取 [0]**；jq `_repull_day` 返 None——**补 `return len(rows)`**。归一改动进限定范围；禁 isinstance 静默守卫。
- **稀疏成功闸——全稀疏轨 4 族**（A-P1-1）：tier1 工厂 `reconcile=False` 出口**与非 _CAP_EMIT_IDS 的 reconcile=True 出口**（forecast/top_list/block_trade 共用行）统一收紧：`failed_dates` 空保持 end_date；非空 ⇒ `cursor_upto = min(failed)-1`（failed 先过 `split(":")[0]`+8 位数字闸取日期串——非日期粒度失败落到 end_date 保持，与 171d-A next_from 闸同构）。落非交易日无害（_cursor_cap 同约定）。回退边界：首日失败 ⇒ cursor=start-1（≤overlap 回退 3 天）——幂等无害，测试钉「回退有界 ≤overlap」。
- **A 股入 scope + DEF-07 早闸**（B-P0-3/A-P1-2/P1-4 联动定稿）：
  - `_RECONCILE_SYMBOL_SCOPE` + `"astock_daily"` 行。
  - **早闸**：SM universe 读取后、expected 循环/local 读**之前**判 `sm_total > budget` ⇒ 跳族（不进 5577×日历 N+1、不付 8.7s local map）——**仍产出 journal 摘要行**（sm_covered/sm_total/budget_exceeded=1）+ 告警复用 `sync.reconcile_budget`（A-P1-2）。
  - **停牌净化窗改全史**（消假缺口依赖链）：`suspend_days_bulk` 窗从 400 自然日 widen 至 `min(floor)`（38.9 万行内存 ~40MB 可控——dev 实测）；不 widen 则 A 股 28 万假缺口股日恒撞闸（A-P1-4/B-P0-3 同撞坐实）。
  - **期望集 N+1 消除**（A-P1-3）：scope 级一次查 `[min(floor), end]` 全量日历、内存按 per-symbol floor 切片（多数标的共享前缀）——即使早闸跳族也现在钉死（调预算启用主张那一刻立即生效）。
- **DEF-08 供给映射**（A-P1-5/B-P1-4）：照 `pull_minute_supply` 先例（137a 同族参数陷阱）新写薄壳 `pull_daily_supply(symbol=, freq=, start=, end=)`（tushare_adapter）；`_SUPPLY_PULL[("bar_daily","stock")]` + `_SUPPLY_PULL_NON_FACTORY` 登记（防 test_batch100 键集断言红）。产线该路径在早闸下暂死码（repull_map={}）——**可测性口径=单测**（spec 明示）。
- **双读复用**（快核备注1）：回补差集预读与收尾对账的 `_local_dates` 同轮复用一次结果（一轮一读，bar_1d stock 侧 ~9s/次省半）。
- **主循环零预算豁免**（快核备注2）：回补差集主循环不挂行预算——豁免依据=手动触发（非 beat 自动）+171d-A 跨度 366 夹天数+差集自限于缺失日（三者天然有界）；预算闸只挂 `_reconcile_dates` repull 段（beat 增量轮自动路径）。

## 1. 实施步骤

1. D31 §4 v2.5 同帧（差集化限稠密白名单+稀疏重放保留）。
2. engine：`_DENSE_BACKFILL_IDS`+tier1 回补差集（含 None fail-open）；via_kind 两站点段式差集（接入表见 §2）。
3. engine：`_reconcile_dates` repull 行预算（stop-after+budget_truncated 回传）+ 三站点 repull_fn 返回值归一。
4. engine：稀疏成功闸（两出口统一，4 族）。
5. engine：scope+astock_daily/早闸/净化窗全史/日历切片。
6. adapters：pull_daily_supply 薄壳+两登记（base.py）。
7. 测试+守门全账（§3）。

## 2. 站点接入表（B-P1-1 补）

| 站点 | 差集化 | 形态 | repull_fn 归一 |
|---|---|---|---|
| tier1 稠密 5 族 | ✓（`_DENSE_BACKFILL_IDS` 门控） | 逐日（date_range 过滤） | int ✓ |
| tier1 稀疏 4 族 | ✗ 整窗重放（D31 §4） | 逐日盲拉 | — |
| daily_batch（astock/listed_fund） | ✓ | **段式**（缺失连续段聚合，父级过滤传子） | tuple→取[0] |
| cb_daily | ✓ | 段式（区间单 fetch） | tuple→取[0] |
| jq | ✗ out-of-scope（自带额度预算体系 :3052——双重闸叠加不做，P2-6 收编声明） | — | None→**补 int**（预算闸在 _reconcile_dates 体内对 jq 增量轮也生效——不补则 TypeError） |
| index/minute | ✗（无回补差集需求声明——index 单 fetch/minute per-symbol） | — | 现返 int/None 核实 |

## 3. 限定范围（5 文件+守门全账）

1. `data_sync/engine.py`
2. `data_platform/adapters/tushare_adapter.py`（pull_daily_supply）
3. `data_platform/adapters/base.py`（_SUPPLY_PULL/_NON_FACTORY）
4. `tests/test_batch171d_d.py`（新）
5. 守门/既有更新入账：**test_batch100 :48 键集断言**（+("bar_daily","stock") 进 NON_FACTORY）；test_batch171d_a `_no_conn_raises` 桩踩 `_local_dates` None 路径（差集 fail-open 行为复核）；scope 表无计数断言 ✓（grep 实证：109/116 membership、110 子集，加行即过）。

## 4. 验收（可证伪）

1. **稠密差集**：dev `cb_daily backfill_from=20250101`（数据已全）⇒ 段表空 `pulled=0`；删 3 日再回补 ⇒ 恰 1 段 3 日。**suspend_d 保持重放**（同窗 ⇒ 逐日重拉非零——差集化不罩稀疏的直证）。
2. **A 股 scope 早闸**：dev 直跑 `_reconcile_symbols()`（不等周日）⇒ `out['astock_daily'].budget_exceeded==1 && sm_covered==5577` + journal 摘要行 + 告警 code=sync.reconcile_budget；**03:33 轮该族增量成本≈SM 读+告警**（无 local map/无 N+1——早闸直证）。
3. **预算闸 stop-after**：mock 每日 3000 行×差集 3 日×预算 5000 ⇒ 拉 2 日停（acc=6000≥5000 允许末日超限）+ budget_truncated + next_from=第 3 日。
4. **成功闸**：mock forecast 窗 3 日 2 日失败 ⇒ cursor_upto=首失败日前；首日失败 ⇒ cursor=start-1（回退≤overlap 断言）。
5. **净化窗全史**：前置=dev 临时调大 `_GAP_ROW_BUDGET["bar_daily"]`（如 6000——早闸跳族下 gaps 永不算，照字执行会撞跳族）；000002 型（155 日停牌段 2015-16）在窗内被净化不产缺口段（dev 直跑断言 gaps 不含停牌日）。
6. **DEF-08**：单测 `fetch_supply("bar_daily","stock",symbol="000001.SZ",...)` 返 DataFrame（产线死码口径）。
7. 全量 pytest + §3 入账全绿。

## 5. 评审要点（v2 剩点）

① 段式差集的段聚合边界（缺日相邻 2 日内合并 vs 严格连续）② 差集化后收尾对账差集恒空 ⇒ D1 痊愈 DEL（A 已直答：回补轮 DEL 是既定语义零增量 ✓）③ expected_days 分母口径（全窗 vs 缺失集——钉全窗+note）④ 早闸后 A 股主张启用路径=调预算+净化窗已全史（依赖序声明）⑤ 日历切片的内存形态（date 列表共享前缀切 index）。
