# 批 101b · 币安永续盘中 bar（小时 / 1min / 15min）

> 设计：`flow/方案/多市场数据接入与代理体系-设计-20261006.md` §二（**该节已按本批实测重写**）
> 前置：批 101（日线）已上产；扩盘决策未做 ⇒ 本批按「有界占用」档落地（见 §二.1）
> 状态：✅ 代码/配置/测试/文档完成（未上产）

## 一、交付物

| 面 | 文件 | 说明 |
|---|---|---|
| adapter | `server/src/data_platform/adapters/binance_adapter.py` | `_pull(sym, interval, ...)` 抽出；`pull_daily` 委托；`pull_minute` 真实现（拒源 interval 名/日粒度）；`to_source_freq`；`_INTERVAL_BY_FREQ` 为 interval 映射**单源**；`capabilities` +3、`capability_decls` +`bar_minute` |
| engine | `server/src/data_sync/engine.py` | `_BINANCE_BAR_SPECS`（四个 sync_id 一张表）+ `_make_binance_bar_handler` 工厂；`_crypto_window(…, first_run_days)`；`_HANDLERS` 20→**23** |
| 迁移 | `server/migrations/versions/0134_binance_perp_minute.py` | sync_config 三行（`schedule='manual'`＝存储闸门）+ sync_kind_config 三行 |
| 映射 | `server/src/quant_common/markets.py` | `SYNC_ID_CAP_MAP` +3（`hist_quote`） |
| 测试 | `server/tests/test_batch101b_binance_perp_minute.py`（新，40 例） | interval 严格性 / 能力声明 / 窗口 / 工厂游标 / 真库对账 |
| 邻接 | `test_batch101_*`、`test_batch99_*`、`test_sync_config_coverage.py` | 见 §三 |

## 二、关键契约（写全，免翻代码）

1. **`_BINANCE_BAR_SPECS`**（唯一真源；`kind/sub_kind` 供 `fetch_supply` 分派，`freq` 同时决定
   表名 `bar_{freq.lower()}` 与 adapter 侧 interval）：

   | sync_id | kind | sub_kind | freq | 表 | 首跑窗口 |
   |---|---|---|---|---|---|
   | `binance_perp_daily` | bar_daily | perp | `1D` | `bar_1d` | 30 天 |
   | `binance_perp_hourly` | bar_minute | perp | `1h` | `bar_1h` | 7 天 |
   | `binance_perp_1min` | bar_minute | perp | `1min` | `bar_1min` | 1 天 |
   | `binance_perp_15min` | bar_minute | perp | `15min` | `bar_15min` | 1 天 |

2. **游标**：`cursor_upto = 实际取到数据的最后一日`（多标的取 max）；全部无数据 ⇒ `start - 1 天`
   （＝旧游标，绝不空转、绝不跳日）。**不是**窗口上界（见 §四.1）。
3. **0 行两态**：整窗落在未发布尾区（`UTC今日 - start ≤ 2 天`）⇒ `log_status='skipped'`（正常等待，
   不进 failed）；窗口更长却 0 行 ⇒ `failed_dates += 'no_rows:…'`（真异常）。
4. **写入语义**：增量 `save_bars`（ON CONFLICT DO NOTHING）、回补 `save_bars_overwrite`
   （OHLCV 覆盖 + `adj_factor` COALESCE 保住）。
5. **`schedule='manual'` 是存储闸门**（§四.2），改 cron 前必须扩盘 + 放宽 `start_floor`。
6. `freq` 只认内部词（`1min`/`15min`/`1h`/`1D`）；源 interval 名（`15m`）**响亮拒绝**。

## 三、门（全部已过）

- ruff 清（只传改动文件）；全量 pytest **2039 passed / 1 skipped**（基线 1999 + 本批 40 例）。
- **迁移 0134 往返**：`alembic upgrade 0134` / `downgrade 0133` 对称（本批用例进 roundtrip 脚本）。
- **真库对账**：`sync_config`/`sync_kind_config` 三行 ↔ `_BINANCE_BAR_SPECS` 逐项一致
  （`tests/test_batch101b_*::TestDbReconciliation`）。
- 邻接修正：`test_batch99` 回补能力 17→20（+start_floor 非空集合）；`test_sync_config_coverage`
  handler 20→23 / 行数 26→29；`test_batch101` 三处（capabilities 集合、`pull_minute` 占位钉
  反转成「真实现诚实性钉」、首跑窗口 30 天含端点）。

## 四、本批抓到并修掉的两个真问题

1. **乐观游标吃日成洞（批 101 遗留，本批修）**：`cursor_upto = 窗口上界` 与批量站发布滞后
   （实测 >1 天）叠加 ⇒ 调度跑在 00:30 UTC 时窗口末日恒 404，游标却推过去 ⇒ 该日永不重试。
   证据：dev `binance_perp_daily` 的 `last_sync_date=NULL / last_status=running`（增量路径从未成功）。
2. **无界增长 vs 已批准的有界档位**：原设计给日调度却只算单轮占用 ⇒ 1min 每天 +450MB、
   prod 可用 8.3G ⇒ 约 18 天打满。落 `schedule='manual'`（§二.1）。

## 五、实证（dev 真库 / 真批量站 / 真 handler）

- 形状：`1h`=24 根、`15min`=96 根、`1min`=1440 根、`1D`=1 根（同一日）；`1D` 首根 open
  与 `1h` 首根 open 一致（84710.50，跨 interval 自洽）。
- **hourly 全宇宙 1004 标的**（7 天窗）：852s → `pulled=saved=131553`、`failed_dates=[]`、
  `cursor_upto=20261004`；`bar_1h` 13.2 万行 / 33MB，`ts ∈ [09-29 00:00, 10-04 23:00]`。
- **15min 回补**（前 40 标的 × 2 已发布日）：24.1s → `pulled=saved=6912`、`cursor_upto=20261004`；
  抽样 `0GUSDT.BINANCE` 恰 192 行（2×96）。
- **1min 回补**（前 30 标的 × 1 已发布日）：38.6s → `pulled=saved=38880`；抽样 `0GUSDT` 恰
  1440 行（10-04 00:00→23:59）。
  （有界回补＝真 adapter/真站/真 handler，仅 `list_symbols()` 截前 N——handler 是逐符号循环，
  同一条代码路径；全宇宙规模由 hourly 单独证。）
- 游标实证：窗口含未发布的 10-05 时，三表实际最大 ts 均落在各自最后已发布时刻
  （hourly 10-04 23:00 / 15min 10-04 23:45 / 1min 10-04 23:59）⇒ 游标 20261004（不跳日）。
- **1min/15min 首跑（1 天窗）实测得 `skipped`**：该窗＝`[UTC昨日, UTC昨日]` 整段未发布 ⇒ 0 行、
  非失败（设计 §2.1 已记录；次日自愈或走 UI 回补）。

## 六、挂账

- `start_floor` 只在 **UI** 生效（`backfillDisabledDate`），后端不校验 ⇒ 直接调 API 可越界。
  30 个同步项同此现状，非本批引入；统一收口另立（已入 `flow/待办.md`）。
- `databus._FREQ_BY_KIND['bar_minute']='1min'` 的降级推断对 1h 是错的——该路径只在实时订阅
  暖机可达，加密无实时腿 ⇒ 挂到实时批（设计 §二.5）。
