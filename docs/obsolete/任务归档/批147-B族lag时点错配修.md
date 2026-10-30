# 批 147 · pool_reconcile B 族 lag 族时点错配修

> 立项 2026-10-25（用户裁定实施）。来源＝批 146 上产步 8 验证定谳：margin_detail「自 20261022 起无任何行」每晚误报。
> 关联：批 130（lag_trade_days 语义：严格早于今日的上一交易日）· 批 92（tier1 窗口族）

## 目标

B 族对账对 lag=1 族（margin_detail）回退一交易日——消每日误报，数据面判定与 T+1 发布节奏对齐。

## 现状（已实证）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | B 族判定「最近交易日该表必须有行」——expected=`_nearest_trade_day(昨日)`，每日 02:03 随 pool 轮跑 | `pool_reconcile.py:203-214` |
| 2 | margin_detail lag=1：晨档 01:03 上界=上一交易日，但上游 T+1 数据**当日晚间才发布** ⇒ 表 max 恒落后 expected 一日 ⇒ 每晚误报 | prod sync_log 10-21~10-23 逐日 success（8958/13440/17924 行连续）；告警 10-23/10-24 连续 |
| 3 | 数据零缺失（修的是判定不是数据） | prod/dev 双侧 sync_log |

## 依赖（就绪）

无前置。

## 引用

- `flow/稳定性检查/观察窗判据汇总.md` §发现 4 margin_detail 条 —— 定谳链
- 批 130 任务文件 lag 语义（`engine.py:292` docstring）

## 产出

1. `pool_reconcile.py` FAMILY_B 结构改带 lag：`margin_detail` 行 lag=1，其余 0；`check_family_b` 对 lag=1 族 expected 再回退一交易日（`_nearest_trade_day(上一交易日)`）。
2. 回归钉（tests/）：①lag=0 族行为不变（expected=最近交易日）；②margin_detail：表 max=expected-1 日 ⇒ 无告警；表 max=expected-2 日 ⇒ 有告警。

## 限定范围

只改 pool_reconcile.py 一处 + tests。**不碰**：sync 引擎 lag 语义、A/C/D 族、_nearest_trade_day 本体。

## 接口契约

- `FAMILY_B: tuple[tuple[str, str, int], ...]`=`(pg_table, date_col, lag_trade_days)`（三元组扩列，唯一 lag=1=margin_detail）。
- `check_family_b() -> list` 签名不变。
- 回滚：git revert（纯代码小批）。

## 验收标准

1. 新钉两测绿 + 全量 pytest 绿
2. 上产后首个 02:03 轮 journal 零「margin_detail 无任何行」告警
3. `grep -n 'margin_detail' server/src/data_sync/pool_reconcile.py` → 带 lag 标注

## mock 方式

patch `pool_reconcile.get_conn`（模块级 :22）构造 COUNT 返回；patch `pool_data._nearest_trade_day` 定向两态。
