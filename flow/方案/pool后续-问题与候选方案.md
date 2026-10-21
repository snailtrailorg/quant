# pool 数据后续 · 问题与候选方案（承接文档）

> 2026-10-21 整理（批 131 上产后遗留）。四子项，数据正确性级 —— 建议作为下一立项批。

## 一、对账缺口（建议优先）

问题：pool 十表不在 109/110 对账范围 —— _reconcile_symbols 只覆盖 bar 表（security_master 宇宙），_reconcile_dates 只到 tier1/bar 家族。pool 失败只体现在 sync_log 一行。

证据：批 131 代码审实证；本次 pool 形态 bug 潜伏至手点触发才暴露。

候选：把 pool 族纳入对账扫描面（不动行结构，轻量）／期四拆 10 行 sync_config（重）。建议先做轻量版。

## 二、_repull_symbol 剥裸码（实盘缺口闭合前）

问题：engine.py:2265 附近 src_sym = vt.rsplit('.')[0] 剥成裸码传 fetch_supply(symbol=)，tushare 的 fetch_supply 无 bar_daily 映射 —— A 股对账重拉恒 UnsupportedFeature（被 catch 计入 errors）。同族隐性故障：实盘缺口永不闭合。

候选：改传 vt 形态（与 fetch 入口归一后的 to_source_symbol 对齐）。

## 三、大池时间盒饿死

问题：run_pool_sync 时间盒 280s 且无续跑/轮转起点，10 表×N 标的、单调用约 0.3s —— 单轮上限约 90 只；超出部分每轮从头开始，尾部标的永久饿死。

候选：按 done_symbols 续跑起点（独立修法，不与形态批次混）。

## 四、vt_to_ts / to_ts_code 双名去重

同一映射两份函数名，合并为单一真源（低优先，防漂移）。
