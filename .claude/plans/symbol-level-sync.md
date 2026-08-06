# 重构：数据同步从"游标驱动增量"改为"完整性驱动"（per-symbol 同步/删除/回补）

## 用户需求（已对齐）
- **同步按钮**（per-symbol）：自动判断该标的当前数据--空则从上市日起全量拉；部分缺失则补缺口；齐全则推进增量。
- **删除按钮**（per-symbol）：清空该标的本地数据，再次同步即完整重建。
- **回补按钮**（per-symbol）：用户指定日期范围重新下载，**覆盖本地已有**（体现手动回补优先级高于增量）。
- **UI**：两级--DataManage 保留 8 数据类型行（管调度周期/启停），点某类型进二级页列出该类型下每只标的，按行操作。
- **全量起点**：每只上市日起（Tushare daily 最早 2010）。
- **执行**：单只 HTTP 同步返回；全市场全量走 Celery 后台任务。

## 关键技术事实（已验证）
- `pro.daily(ts_code='X', start_date='20100101')` 一次拿全历史 0.4s（000001.SZ 3950 天）-> **按标的拉远优于按日拉**。
- `save_bars` 是 `ON CONFLICT DO NOTHING`（补缺用）-> 回补覆盖需 `DO UPDATE` 版本。
- `asset_static_info.list_date` 已记录（astock_list 同步时入库），可做 per-symbol 上市日判断。
- `trade_cal` 仅 2026 当年 -> 完整性校验缺口的"预期交易日"需按标的的本地首末日 + trade_cal 算，不强依赖多年历。
- Celery worker 未启动；`task_soft_time_limit=300`（5min）会杀全量任务 -> 需为全量任务单独放宽 `soft_time_limit`/`time_limit`。

## 改动清单

### 1. `src/data_sync/engine.py` 重构（核心）
新增 per-symbol 三函数 + 保留类型级 sync 作批量入口：
- `sync_symbol(sync_id, symbol, mode='auto')`：单标的智能同步。
  - `auto`：查该标的 bar_1D 首末日期 + `list_date`。空 -> 从 list_date（或 2010）全量；有 -> 增量从 max(ts)+1 到今天。用 `pro.daily(ts_code=, start=, end=)` 拉取。
  - 返回 `{status, pulled, saved, range:[首,末], mode_used:'full'|'incremental'}`。
- `backfill_symbol(sync_id, symbol, start, end, overwrite=True)`：per-symbol 回补。
  - 用 `pro.daily(ts_code=, start=start, end=end)` 拉，`overwrite=True` 时调 `save_bars_overwrite`（DO UPDATE）覆盖本地。
  - 返回 `{status, pulled, saved, range}`。
- `delete_symbol(sync_id, symbol)`：`DELETE FROM bar_1D WHERE symbol=%s`。返回 `{deleted}`。
- `sync_all(sync_id)`：全市场全量（Celery 调用）。遍历 `asset_static_info`/`cb_basic_info`/`etf_basic_info` 的全部 ts_code，对每只调 `sync_symbol(mode='auto')`，累计进度写 `sync_log`/Valkey（前端轮询）。
- 保留现有 `sync(sync_id, backfill_from)` 作为**类型级**入口（按日增量，Celery beat 定时用），不动。
- 通用工具：`_get_list_date(sync_id, symbol)` 从静态信息表查上市日；`_expected_trading_days` 已有复用。

### 2. `src/data_platform/schema.py` + `db.py`
- 加 `BAR_TABLE_INSERT_OVERWRITE`（`ON CONFLICT (symbol, ts) DO UPDATE SET open=EXCLUDED.open, ...`）。
- `db.py` 加 `save_bars_overwrite(freq, rows)` 函数。回补覆盖用。

### 3. `src/web_api/main.py` 加 4 个端点
- `GET /api/sync/symbols/{sync_id}`：列某数据类型下全部标的 + 每只本地数据天数/首末日（供二级页展示）。分页（标的几千只）。
- `POST /api/sync/symbol/{sync_id}/{symbol}`：单标的同步（HTTP 同步，0.5s 返回）。body 可带 `mode`。
- `POST /api/sync/symbol/{sync_id}/{symbol}/backfill`：单标的回补。body 带 `start`/`end`。
- `DELETE /api/sync/symbol/{sync_id}/{symbol}`：删单标的数据。
- `POST /api/sync/all/{sync_id}`：触发全市场全量（提交 Celery，立即返回 task_id）。前端轮询进度。
- 类型级 `/sync/trigger/{sid}` 保留（beat 定时用）。

### 4. Celery 任务 `src/scheduler/tasks.py`
- 加 `sync_all_symbols(sync_id)` 任务，`@app.task(soft_time_limit=3600, time_limit=4200)` 放宽到 70 分钟，调 `engine.sync_all(sync_id)`，进度写 Valkey key `sync:progress:{sync_id}`。
- 现有 `task_soft_time_limit=300` 全局默认不动，只给此任务单独覆盖。

### 5. 前端两级 UI `src/web_ui/src/views/DataManage.vue` + 新组件
- DataManage.vue：现有 8 数据类型表格保留；每行"操作"列加"管理标的"按钮，点进路由到 `SymbolManage.vue`。
- 新建 `src/web_ui/src/views/SymbolManage.vue`：路径 `/data-manage/:syncId`。
  - 顶部：数据类型名 + "全量同步全部"按钮（提交 Celery，轮询进度条）。
  - 表格：每行一只标的（symbol/name/list_date/本地天数/首末日/状态），操作列：同步/回补/删除三个 per-symbol 按钮。
  - 分页（标的几千只）。
  - 回补弹窗：输入起止日期。

### 6. 验证
- 单只同步：对 002387.SZ 删后同步，验证从 list_date 起全量拉取。
- 缺口补全：对已有 21 天的标的，同步应只拉近几天增量。
- 回补覆盖：对已有数据的标的回补某范围，验证本地被覆盖（DO UPDATE）。
- 全量 Celery：对小数据类型（如某 ETF 子集）触发全量，验证进度轮询。

## 本棒范围与不做
**做**：改动 1-6（per-symbol 三函数 + overwrite + 4 端点 + Celery 全量任务 + 两级前端 UI + 验证）。

**不做**：
- 启动 Celery worker 作为常驻服务（提供启动命令，但本棒靠手动启 worker 验证全量；常驻化属部署 M5）。
- 类型级 `sync(sync_id)` 按日增量逻辑保留给 beat 定时，不改其语义。
- 可转债/ETF 的 list_date 完整性（cb_basic_info/etf_basic_info 已有 list_date 列，直接用；个别空值回退到固定起点 2010/2020）。

## 验收标准
1. DataManage 点"管理标的"进二级页，列出该类型全部标的 + 每只本地天数。
2. 单只空标的点同步：从上市日起全量拉取（首末日正确）。
3. 单只有数据标的点同步：只拉近几天增量，不重复拉全历史。
4. 回补指定范围：本地已有数据被覆盖（验证 DO UPDATE 生效）。
5. 删除单只：bar_1D 该 symbol 行清空，再同步完整重建。
6. 全量同步：提交 Celery 后台执行，前端显示进度，完成后数据齐全。

## 风险与回滚
- save_bars_overwrite 是新函数，不影响现有 save_bars（DO NOTHING）。
- per-symbol 端点是新增，不破坏现有类型级端点。
- 全量 Celery 任务单独放宽超时，不影响其他任务。
- 按标的拉历史对 Tushare 调用频次友好（5531 次 vs 按日 3950 次/年），限频风险低。
