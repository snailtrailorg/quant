# 批 79 · 删除 shadow 对账（同源自检无意义）

> 立项 2026-09-29（观察 D1 触发）。用户裁定：shadow 行情主备对账 = 拿 Tushare 和平台加工过的 Tushare 比（主备底层同源 `pro.daily`），抓不了源数据错、也抓不了换算约定错（「一致地错」），价值天花板太低，还养一个会假绿的机制——**删除**。
> **SM 对账（sm_reconcile）保留**（security_master vs static_symbols 两张表互对，不涉行情同源问题）。
> **validate_bar_quality 保留**（批 71 单源内部一致性：跳空/断点，在 `tushare_adapter.py:469`，不依赖备源——这才是真正有意义的质量检查）。

## 删除清单

### 后端
1. `server/src/data_platform/quality.py`：删 `load_whitelist`/`_match_entry`/`_within_tolerance`/`diff_rows`/`_month_usage`/`_sample_symbols`/`_fetch_backup_daily`/`run_shadow_check`/`whitelist_stats`/`whitelist_verify_check` 共 10 函数；**保留 `sm_reconcile`**（+ 其依赖 `to_vt_symbol`/`get_conn`）。
2. `server/src/scheduler/tasks.py`：`quality_shadow_check`(1706) 瘦身为仅 SM 对账（删 run_shadow_check/whitelist_verify_check 调用，保留周一 sm_reconcile）；删 `quality_cleanup`(1725)。
3. `server/src/scheduler/app.py`：beat 注册 `quality_shadow_check` 同步调整（任务名/说明）。
4. `server/src/web_api/routes/quality.py`：删 `/shadow-diff`/`/whitelist-stats`/`/whitelist-verify/{id}`/`/shadow-check` 4 端点；**保留 `/sm-reconcile`/`/lineage/{signal_id}`**。
5. `server/src/alert_notify/runbook.py`：删 `quality.budget-exhausted`/`budget-throttle`/`real-diff`/`whitelist-stale` 4 条。

### 迁移
6. 新迁移 `0114`：`DROP TABLE shadow_policy, shadow_diff`（表由 0110 创建；破坏性 DDL，随版走 allow_contract）。

### 白名单文件
7. 删 `server/src/data_platform/whitelist_semantic.json`、`whitelist_coldswitch.json`。

### 前端
8. `web/src/api.js`：删 `getQualityDiff`/`getWhitelistStats`/`verifyWhitelist`/`triggerShadowCheck`（309-311,314）；保留 `getSmReconcile`/`getLineage`。
9. `web/src/views/QualityCheck.vue`：删 shadow diff 列表段 + 白名单治理段，**保留 SM 差集段**（页签瘦身）。
10. `web/src/locales/`：删 `quality.` diff/whitelist 词条（保留 sm 相关）。

### 测试
11. `server/tests/test_quality.py`：删 `TestDiffRows`/`TestBudgetFloor`/`TestWhitelistVerifyCheck` 及 shadow 相关；保留/改写 SM 对账测试（如存在）。

### 文档
12. `server/src/data_platform/schema_expectations.txt`：删 `shadow_diff`/`shadow_policy` 2 行（重新生成）。
13. `flow/待办.md` + 批 71/72 任务文件：gate 判据删「shadow 全白名单吸收」，只留 `validate_bar_quality` 的 `[quality]` 日志甄别。

## 保留清单（性质不同，不误删）
- `validate_bar_quality`（`tushare_adapter.py:469`，批 71 单源质量校验）
- `sm_reconcile`（SM 标的清单对账）
- `/lineage/{signal_id}`（signal→order_log 审计链贯通）

## 影响面
- **批 71 gate**：原判据=「`[quality]` 日志甄别 + shadow 16:05 全白名单吸收」，删 shadow 后只留 `[quality]` 甄别——gate 判读反而更可靠（不挂假绿机制）。
- **前端**：/dataops 第四 tab「对账」瘦身为只 SM 对账。

## 验收标准
1. 全量 pytest 绿（1537 - shadow 相关测试数）
2. `ruff check src` 全绿 + 前端 `npm run build` 绿
3. grep 全仓无 `run_shadow_check`/`shadow_policy`/`shadow_diff`/`whitelist_semantic` 残留（除迁移 0114 的 DROP）
4. `/sm-reconcile`/`/lineage` 端点仍工作；`sm_reconcile` beat（周一）仍调度
5. 迁移 0114 双向（up/down）验证通过
