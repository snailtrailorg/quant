# 批 171d-D · 回补空洞差集 + per-date 预算闸 + 稀疏成功闸 + A 股入对账 scope（D31 批 D）

> **依据**（引用 ≤2）：`docs/design/D31-窗口模型统一.md` §4/§3（稀疏成功闸）；`docs/design/D30-同步缺陷账本.md` DEF-05/DEF-07/DEF-08。
> **性质**：D31 第三代码批（A/B 已上产：1fbad91/2839e8f）。吸收 171c 腿 2（A 股入 scope）。
> **淘汰**：上产后归档。

## 0. 契约（自包含）

- **稠密族回补差集化**：`backfill_from` 轮的 tier1/_sync_via_kind 族回补不再「整段重放」——`_reconcile_dates` 已有差集+内联重拉机制，**回补轮把它从「收尾自检」升为「主路径」**：先查 local 差集，只拉缺失日（幂等不重拉已有日）。现状回补=逐日盲拉（suspend 13 年 388k 行全量重放即此形态——稠密族回补 5 年会重拉 ~1200 已有日）。
- **per-date 预算闸（新接线）**：`_reconcile_dates` 加行数预算（`_GAP_ROW_BUDGET` 同表阈值，per-date 用 `bar_daily` 档 5000）——**回补差集超阈 ⇒ 截断本轮重拉至预算内+`truncated` 语义**（与 171d-A span 截断同语义链，next_from 续跑）。现状 per-date 路径只有 `max_repull` 次数帽（:3052 由调用方传）——次数≠行数（一行=全市场当日），预算闸按**落表行数**计。
- **稀疏成功闸**：tier1 工厂 `reconcile=False` 族（suspend_d）游标推进条件收紧——`failed_dates` 非空 ⇒ `cursor_upto = 最后连续成功日`（现= end_date 无条件推）。171b 立项时的「成功闸」承诺兑现（D31 §3）。
- **A 股日线入 per-symbol scope（DEF-05/08）**：`_RECONCILE_SYMBOL_SCOPE` 加 `"astock_daily": ("bar_daily", "stock", "1D", "bar_1d", "Asia/Shanghai", "stock")`——171b 净化口（停牌过滤+delist 截断）在 per-symbol 对账路径激活（已预置死码转活）；DEF-08 修复映射：`_repull_symbol` 的 bar_daily/stock 供给路径核实/补（`_fetch_supply` per-symbol 支路）。
- **预算 vs 标的数（DEF-07）**：A 股 ~5577 标的 > bar_daily 预算 5000——入 scope 前预算闸判据改「预算 ≥ 单族标的数 ⇒ 才主张」（超阈该族 `budget_exceeded` 跳过不主张，告警）。

## 1. 实施步骤

1. **A 股入 scope**：scope 表 +1 行（astock_daily）；`_repull_symbol` 的 stock 供给路径核实（DEF-08——`_fetch_supply(kind='bar_daily', sub='stock', symbol=...)` 支路存在性）；预算闸 DEF-07 判据（标的数>预算 ⇒ 整族跳过+告警，不主张零星段）。
2. **回补差集化**：tier1 工厂回补分支——拉取循环前查 `_local_dates`（窗内），`date_range` 过滤为缺失日集；零缺失 ⇒ 直接跳到对账收尾（`pulled=0`）。via_kind 族同构（daily_batch/cb_daily）。
3. **per-date 预算闸**：`_reconcile_dates` 的 repull 段加行数累计（repull_fn 返回行数——`_pull_day` 现返行数 ✓），超阈停拉+返回体 `budget_truncated: True` + handler 转 `next_from`（续跑语义接入 171d-A 链）。
4. **稀疏成功闸**：tier1 `reconcile=False` 出口的 `cursor_upto`——`failed_dates` 空保持 end_date；非空 ⇒ 从窗末回扫首个失败日前一连续成功日（简化：`min(failed)-1` 与「连续成功末」取保守小值——用 `_next_from` 已有逻辑反推）。
5. 守门：scope 表断言（grep 现有 test——test_batch110 系列）、回补差集行为级、预算截断、成功闸三态。

## 2. 限定范围（4 文件+守门）

1. `data_sync/engine.py`（scope+1 行/回补差集两站点/预算闸/成功闸）
2. `tests/test_batch171d_d.py`（新）
3. 守门：scope 表相关既有断言（找到改）+ 链头钉无（无迁移）
4. 既有测试复核：test_batch110（scope 数）、_reconcile_dates 系（回补语义变化——**回补轮行为改变是本批目的**，相关测试期望更新入账）

## 3. 验收（可证伪）

1. **差集回补**：dev `suspend_d_sync backfill_from=20240101`（数据已全）⇒ `pulled=0`（零缺失零拉取——现状会重拉 22 日）；删 3 日数据再回补 ⇒ 恰拉 3 日。
2. **A 股 scope**：03:33 对账轮 journal 出现 astock_daily 摘要（sm_covered~5577/缺口段结构）；DEF-07：预算 5000<标的 5577 ⇒ `budget_exceeded` 跳过+告警（**首个轮次预期即此**——预算闸先拦，调预算或分批后才有主张）。
3. **per-date 预算闸**：mock 差集 6000 行/预算 5000 ⇒ 恰拉 5000 停+`truncated/next_from`。
4. **稀疏成功闸**：mock suspend_d 窗 3 日中 2 日失败 ⇒ cursor_upto=连续成功末日（非 end_date）。
5. 全量 pytest 绿 + 既有期望更新全部入账声明。

## 4. 评审要点

① 回补差集化对 `_reconcile_dates` 收尾的交互（拉取循环已过滤缺失日 ⇒ 收尾差集恒空——对账仍跑还是回补轮跳过收尾）② A 股 scope 的 SM 宇宙（stock 5577 含退市——delist 截断 171b 已备 ✓）与 03:33 轮时长（5577 标的×期望集——批 110 读取契约成本核算）③ 预算阈值按行 vs 按族（DEF-07 判据实现形态）④ 成功闸与 171d-A next_from 的语义一致性 ⑤ DEF-08 供给路径若缺——补映射的触面（`_repull_symbol` 还是 adapter）。
