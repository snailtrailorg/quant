# 盲审 · 复权因子 · 代理 F（2026-08-18，含本地 PG 基准 + 真实 Tushare 实测）

> 评审对象：4962824。实测取证：本地 bar_1d 13.4M 行/5546 标的/**4031 个 NULL 因子日**跑 UPDATE 基准；真实 token 调 adj_factor(trade_date=) 验接口。结论：**2 致命 3 严重——"一键回填"承诺在真实存量上跑不完/跑完不收敛/可被一键清零，全部确认后已修**。

## 致命（已修）
- F-F1 backfill 必然超 soft_time_limit：`ts::date=%s` 用不上索引实测 3.68s/日 × 4031 日 ≈ 4.7h ≫ 1h。修：范围谓词 `ts>=d AND ts<d+1`（实测 **18x** → 0.20s/日，全量 ~50min）+ 限时放宽 soft 7200。
- F-F2 backfill_symbol 单标的回补把已回填因子**静默清回 NULL**：_daily_to_rows 不带 adj_map + overwrite DO UPDATE。修：①BAR_TABLE_INSERT_OVERWRITE 的 adj_factor 改 COALESCE（EXCLUDED 空→保留原值，全路径兜底）②backfill_symbol 接 pull_adj_factor_by_code（原为死代码）。

## 严重（已修/已记）
- F-S1 ETF 命名空间不匹配（实测 adj_factor 只含沪深京股票 5553 行、ETF 0 命中——ETF 因子在 fund_adj）：etf_daily join 永久空转 + backfill 永不收敛。修：dates 查询限定 asset_static_info（股票行）。
- F-S2 qfq 只修一半：platform.py get_bar/ensure_daily 默认仍 qfq（平台化公共接口）。修：默认 None。
- F-S3 updated 虚报（提交行数含 0 命中）。修：cursor.rowcount（驱动 -1 防御）。

## 一般核对（要点/处置）
- 降级请求无闩锁天天白打 → 1h 闩锁短路（已修）；告警多条（celery 并发 2×进程级闩锁）→ 有限可容忍
- 事务/幂等 ✓（每日 commit/崩溃当日回滚/只填 NULL）；占位符序一致 ✓
- degraded → complete_task("failed") 三处口径不一 → 记待办（低优）
- 进度键无消费端点 → 已补 GET /api/sync/adj-factor-backfill/progress
- 测试副作用（真实发通知）→ 已 mock；None/NaN 因子毒化点 → 已补测试
