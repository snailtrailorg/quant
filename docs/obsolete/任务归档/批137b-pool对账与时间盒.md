# 批 137b · pool 族对账覆盖与时间盒 · 已上产

> 立项 2026-10-22（原 137 拆分）。**上产 `202610222339-d03e8f2`**（staging+prod 双绿）。
> 判据按**表族**定（用户裁定：不同表用途不同，不能一套规则套全部）；时间盒取**方案 A（无状态按最陈旧排序）**。

## 一、判据（落地形态）

| 族 | 表 | 判法 | 数据不足时 |
|---|---|---|---|
| **A 逐标的日频** | `cyq_chips` | 逐标的判「最近一条 < 上一交易日」，且**只判当日有行情的标的**（`bar_1D` 当日有行 ⇒ 该有筹码）——停牌/退市/次新不判 | 交易日历读不到 ⇒ 跳过 |
| **B 全市场按日快照** | `stk_limit`/`moneyflow`/`margin_detail`/`top_list`/`block_trade`/`st_list` | 「最近交易日该表行数 > 0」，**不按标的判** | 同上 |
| **C 静态清单** | `astock_list`/`listed_fund_list`/`cb_basic`/`trade_cal`/`namechange_sync`/`concept_sync` | 新鲜度 > 7 天未成功 ⇒ 报；成功但 0 行 ⇒ 不报 | — |
| **D 事件/季频** | 财务四表 + `top10_holders`/`dividend`/`pledge_stat`/`share_float`/`stk_holdernumber` | **不按标的判**；连续 6 轮全池 0 行且无抓取错误 ⇒ 疑似上游断供（streak 存 `system_config`） | 时间盒截断轮不计数 |

**实现纪律**：符号列/日期列一律从 `sync_kind_config.pk_cols` 派生（池表 = `ts_code`，**不是 `symbol`**；bar 表才是 `symbol`）——代码审 P0-1 血证：首版把 `symbol` 写死，SQL 直接 `column does not exist`，排序退化成原固定顺序（等于本批没生效）且 A 族会 100% 假报。

## 二、时间盒（无状态按最陈旧排序）

- 每轮起点不固定：查各 `(表, 标的)` 最近行日期，**按「与该表自身最新日期的差」降序**排（最陈旧先跑）。
- **为什么用相对差而不是绝对日期**（代码审 P0-3）：事件表整体停在旧期（`pledge_stat` 2024 / `dividend` 旧期），绝对日期会让它们每轮霸占预算、`cyq_chips` 永不执行。相对差让「只是没新数据」的表不抢预算。
- 同陈旧度时按族权重（A > B > C > D），A 族日频可自愈优先。
- 无记录 = 极陈旧（排最前）；无游标、重启安全、池子增删自动适应。
- 可观测：每轮 `[pool_reconcile]` 一行（findings 摘要）；无 findings 记 info「A/B/C/D 四族检查通过」。

## 三、触发面

- **只在调度轮**跑（`full` 校准与手动定向回补不跑）：那两类轮窗口语义不同，会错判；且保持批 95「full 轮不查日历」不变量。
- 时间盒**截断轮照跑 A/B/C**（只读、与截断无关）——否则池大时每轮必截断，对账成死功能；D 族 streak 仅整轮走完才计数。

## 四、告警面

- findings → `logger.warning("[pool_reconcile] …")` + 站内告警（`notify`，**1 小时节流**，存 `system_config`）。
- **不自愈**：本批只做「看得见」。A 族重拉路径已由 137a 修通（`_repull_symbol` 源格式），但本批**未**把缺口写入 `sync_gap`（表结构可用，但消费方只服务 bar 重拉 ⇒ 写了也没人拉）——留作 137c 候选。

## 五、限定范围（实际改动）

- 新增 `server/src/data_sync/pool_reconcile.py`（族表 + 判法 + 排序 + 告警节流）
- `server/src/data_sync/pool_data.py`（循环改 `stale_order`、per_table_saved、收尾对账钩子）
- 新增 `server/tests/test_batch137b_pool_reconcile.py`（25 例，含 3 个 P0 判别钉）

## 六、验收（实测）

1. 族映射：B 族显式 6 表（排除 `cyq_perf`/`forecast`——前者实测最新交易日 0 行、后者事件型）✅
2. SQL 列名钉：`last_dates` 用 `ts_code`（断言 SQL 不含 `symbol`）✅
3. 排序钉：落后于本表 tmax 的标的先跑；无记录最前；同陈旧度 A 族优先 ✅
4. 截断轮不跑 D 族（streak 不失真）✅
5. 全量 2823 绿；ruff 干净（src 两处 F401 为既有债，不在本批）
6. prod 冒烟：部署后触发一轮 `pool_data` ⇒ `success saved=4713 dur=4.9s`（无 error）

## 七、挂账

- 缺口未落 `sync_gap`（见 §四）
- `stale_order` 每轮 10 条 `GROUP BY MAX(date)`；`income` 按 `ann_date` 取最新但索引是 `(ts_code,end_date)` ⇒ 大表可考虑改判 `end_date`