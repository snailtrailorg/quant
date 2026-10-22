# 批 137b · pool 族对账覆盖与时间盒（判据待设计 · 挂起）

> 立项 2026-10-22（原批 137 拆分）。**状态：设计未定，暂不编码**——盲审指出原方案判据不可实施，须先重写判定口径。

## 问题（盲审实证）

### ① pool 十表不在 109/110 对账范围

- `_reconcile_symbols` 只覆盖 bar 表（security_master 宇宙，`engine.py:1495`）；`_reconcile_dates` 只到 tier1/bar 家族。
- 直接套用会**大面积假缺口**：十表中 5 张事件/季频表（`top10_holders`/`dividend`/`pledge_stat`/`share_float`/`stk_holdernumber`）**无期望日集**；财务四表按公告日而非交易日。
- `_read_sync_kind`（`engine.py:2717`）不读日期列，`sync_kind_config` 无该字段 ⇒ 扩判据要新增列或代码常量；常量若放 `pool_data` 会与 `engine` 形成循环 import（`pool_data.py:28` 已 import engine）。
- `sync_gap` 表结构（0140:41-56）可行，但其消费方（`_repullable_map`/`_repull_symbol`）只服务 bar 重拉 ⇒ pool 缺口落表后**无自愈路径**，只能告警。

**候选判据（待定稿）**：per`(table, symbol)` 最近日期陈旧度 + 连续 N 轮 0 行 streak，**只对 `cyq_chips` 与财务四表**判缺口；事件表不判（无期）。池表 symbol 存 ts_code 而全局惯例 vt——须显式处理。

### ② 大池时间盒饿死

- `run_pool_sync` 时间盒 280s 无续跑起点；10 表 × 0.3s 限速（`data_source.py:132-135`）≈3s/只 ⇒ 单轮 ≤93 只，超出部分每轮从头开始。
- 5 张全史表（`_window_of` 非增量 → `(None, None)`）每轮重拉全史，实际更贵。
- **候选修法（推荐，无状态）**：按 `(table, symbol)` 最近行日期升序排（最陈旧先跑）——重启安全、自愈、零状态。
  若要游标则**必须落 DB**（进程内变量重启即回顶部，饿死复现）。
- 注入时钟**已有先例**（`tests/test_pool_data_incremental.py:126-149` patch 模块 `time.time`）⇒ 不加 `now_fn` 参数。

## 待办

1. 定稿 ① 的判据（陈旧度阈值？streak 轮数？哪些表纳入？symbol 形态怎么统一）
2. 定稿 ② 的修法（无状态排序 vs DB 游标）
3. 定稿后按八步法走：方案 → 双盲审 → 编码
