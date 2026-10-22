# 批 137 · pool 数据后续四子项（对账缺口 / 裸码重拉 / 时间盒饿死 / 双名去重）

> 立项 2026-10-22（批 131 上产后遗留，见 `flow/方案/pool后续-问题与候选方案.md`）。数据正确性级。

## 问题（四子项，按优先级）

| # | 子项 | 现状 | 影响 |
|---|---|---|---|
| ① | **pool 十表不在 109/110 对账范围** | `_reconcile_symbols` 只覆盖 bar 表（security_master 宇宙）；`_reconcile_dates` 只到 tier1/bar 家族 | pool 族失败只留 sync_log 一行，无缺口检测——批 131 的形态 bug 潜伏至手点触发才暴露，即此缺口 |
| ② | **`_repull_symbol` 剥裸码** | `src_sym = vt.rsplit(".")[0]` 传 `fetch_supply(symbol=)`，而 tushare 的 fetch_supply 无 bar_daily 映射 ⇒ 恒 UnsupportedFeature（被 catch 计入 errors） | A 股对账重拉永不生效 ⇒ 缺口无法自愈 |
| ③ | **大池时间盒饿死** | `run_pool_sync` 时间盒 280s 且无续跑起点；10 表 × N 标的、单调用约 0.3s ⇒ 单轮约 90 只，超出部分每轮从头开始 | 大池尾部标的永久饿死 |
| ④ | `vt_to_ts` / `to_ts_code` 双名 | 同一映射两份函数名 | 漂移风险（低） |

## 产出

1. **①对账覆盖（轻量版优先）**：把 pool 族纳入 `_reconcile_symbols` / `_reconcile_dates` 扫描面——**不动 sync_config 行结构**（期四拆行仍是按需，不在本批）。判据：池内标的缺日可被检出并落 sync_gap（或等价面）。
2. **②裸码修正**：`_repull_symbol` 传 vt 形态（与批 131 的 fetch 入口归一 `to_source_symbol` 对齐）；tushare `fetch_supply` 若仍无 bar_daily 映射，补映射或改走 `fetch` 契约（择一，写进实现说明）。
3. **③续跑起点**：`run_pool_sync` 记录 `done_symbols`（或游标下标）作下一轮起点，超时不再从头开始；轮转语义须写明（一轮结束重置 / 连续几轮无进展时告警）。
4. **④改名去重**：保留单一函数（`to_ts_code`），`vt_to_ts` 保留为薄别名或全仓替换后删（择一，改动面 ≤5 文件）。

## 限定范围

改：`server/src/data_sync/engine.py`（①②③）、`server/src/data_sync/pool_data.py`（③）、`server/src/data_platform/schema.py`（④）、`tests/`。
不碰：sync_config 行结构（期四）、pool 十表 schema。

## 验收标准

1. ①：构造池内某标的某日缺行 ⇒ 对账能检出（单测 mock）；且不误报非交易日
2. ②：`_repull_symbol` 对 A 股缺口的重拉路径**真跑通**（mock 断言 fetch 收到 vt 形态，或集成用例 saved>0）
3. ③：mock 时间盒超时 ⇒ 第二轮从上次断点继续，不从头开始；连续无进展触发告警
4. ④：全仓 grep 只剩一个实现名；单测覆盖映射矩阵不回归
5. 全量 pytest 绿

## mock 方式

pool 表用 mock conn/rows；`run_pool_sync` 用可注入的时钟（若现无，加 `now_fn` 参数，不动生产默认值）。
