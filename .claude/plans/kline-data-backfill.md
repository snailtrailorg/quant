# 修复：K线图只有 7/20 以后数据（数据同步静默丢日 + 无回补机制）

## 根因（已诊断，DB 实证）

1. **静默吞异常**：`src/data_sync/engine.py` 各 handler 的 `except Exception: continue` 把 `pro.daily(trade_date=...)` 失败的日期**静默跳过，无日志、无计数**。6/26-7/17 共 13 个交易日 `pro.daily` 调用失败被吞，sync_log 只显示最终 success + 5 天数据，看不出丢日。
2. **无回补机制**：增量逻辑 `start = last_sync_date + 1`，同步完推进 `last_sync_date` 游标。再点同步 `start >= today` 直接 `return 0,0`。**历史缺口永远补不回来**。
3. **无完整性校验**：同步后不比对"预期交易日 vs 实际入库天数"，缺口不可见。
   - 实证：`astock_daily` last_sync_count=27627 ≈ 5天×5531，A股在 bar_1D 仅 7/20-7/24 五天（600000.SHSE 是早期单独测试的 134 天，例外）。

## 修复方案（根本解，三处改动 + 一次回补验证）

### 改动 1 · `src/data_sync/engine.py`（核心）
- **抽取通用按日循环** `_sync_by_trade_date(pro_api_fn, save_fn, start, end, sleep)`，消除 `_sync_astock_daily`/`_sync_astock_basic`/`_sync_etf_daily` 三处重复逻辑。
- **去静默吞异常**：`except` 不再 `continue` 无痕，改为记录失败日期到 `failed_dates` 列表 + 累计 `failed_count`；单日期失败不中断整体，但结果显式返回缺口。
- **回补支持**：`sync(sync_id, backfill_from=None)` 新增可选参数。有 `backfill_from` 时：`start=backfill_from`，`end=today`，且**不推进 `last_sync_date` 游标**（只回补历史，不动增量进度）。无则保持原增量。
- **完整性校验（同步后）**：用已有 `data_platform.db.get_trade_calendar(year)` 算区间内预期 SSE 交易日数，比对实际成功拉取的日期数，缺口日期列表返回给调用方 + 写 sync_log。
- sync_log 写入增加 `failed_dates`/`expected_days`/`actual_days` 字段（schema 见改动 3）。

### 改动 2 · `src/web_api/main.py`
- `POST /api/sync/trigger/{sid}` 加可选 query `backfill_from=YYYYMMDD`，透传给 `sync()`。
- 返回结果增加 `failed_dates`/`expected_days`/`actual_days`（前端展示缺口）。

### 改动 3 · DB schema：sync_log 加 3 列
- `ALTER TABLE sync_log ADD COLUMN failed_dates TEXT, expected_days INT, actual_days INT`
- 让历史缺口可追溯。用 `ALTER TABLE IF NOT EXISTS` 幂等，现有代码无需改。
- sync_config **不加** backfill_from 字段（走 trigger query 参数更简单，避免污染配置态）。

### 改动 4 · `src/web_ui/src/views/DataManage.vue`
- 表格每行加"回补"按钮（仅 `incremental` 模式 + astock_daily/astock_basic/etf_daily/cb_daily 显示），弹 `ElMessageBox.prompt` 输入起始日期（默认 30 天前），调 trigger 带 `backfill_from`。
- 同步日志表加 `failed_dates`/`缺口` 列展示（缺口>0 时红色 tag）。
- 同步结果 toast 展示缺口数（如"入库 X 条，缺口 Y 天：[...]"）。

### 改动 5 · 执行一次真实回补验证
- 修完机制后对 `astock_daily` 触发 `backfill_from=20260626`，回补 6/26-7/19 的 13 个交易日。
- 验证：回补后 bar_1D 中 A股 应有 6/26-7/24 约 21 天；浏览器点 A股 K线应显示更多天数。
- 若回补时 `pro.daily` 仍失败（限频等），改动 1 的去静默会让失败原因暴露在 sync_log，再针对性处理（不阻塞本棒）。

## 本棒范围与不做

**做**：改动 1-5（engine 核心修复 + 回补端点 + sync_log 字段 + 前端回补入口 + 一次真实回补验证）。

**不做（记入下一步）**：
- `data_continuity_check` 定时任务修正（`expected=5` 写死不准、逐只补采慢）—— 属"定时巡检"维度，与"同步后即校验"不同，下棒单独处理。
- `pro.daily` 在 6/26-7/17 失败的具体原因排查（限频/接口波动）—— 修复后会从 sync_log 暴露，再判断是否需调限频策略。

## 验收标准
1. `except` 静默吞异常消除：人为造一个失败日期（如断网/限频），sync_log 出现 failed_dates 记录，不再静默。
2. 回补机制：trigger 带 backfill_from 后，last_sync_date 游标不变，bar_1D 数据补齐到 backfill_from 起。
3. 完整性校验：返回 expected_days/actual_days，缺口日期可见。
4. 真实回补后浏览器 K线显示 ≥20 天（当前 5 天）。
5. 前端回补按钮 + 缺口展示可用。

## 回滚安全性
- `save_bars` 用 `ON CONFLICT (symbol, ts) DO NOTHING`，回补重复日期自动跳过，不会重复写。
- sync_log 加列用 `ADD COLUMN IF NOT EXISTS`，幂等无风险。
- backfill_from 走 query 参数，不落库，不影响现有增量逻辑。
