# 批 101b · 币安永续盘中 bar（小时 / 1min / 15min）

> 立项 2026-10-06（威廉姆：小时线先拉一周、分钟线先拉一天，功能验证优先）。来源＝批 101 日线打通后，加密盘中粒度的功能验证需求。
> 关联：批 101（日线，已上产）· 设计真源 `flow/方案/多市场数据接入与代理体系-设计-20261006.md` **§二**（该节已按本批实测重写）
> 状态：✅ 代码/配置/测试/文档完成（**已 merge 至 main `64821d2`**；上产随下个 release）

## 目标

一句话：把 `BinanceAdapter` 从「只有日线」泛化为**一条实现吃四个 interval**，并落地
`binance_perp_hourly`（7 天）/ `binance_perp_1min`、`binance_perp_15min`（各 1 天）三个同步项，
使 `bar_1h`/`bar_1min`/`bar_15min` 首次真实落库（自 0064 建表起从未落过一行）。

## 现状（已实证）

| # | 事实 | 锚点 |
|---|---|---|
| 1 | 批 101 的 handler 只做日线；`pull_minute` 是 `NotImplementedError` 占位 | `binance_adapter.py`（旧 `pull_minute`） |
| 2 | 币安批量站**发布滞后 >1 天**：10-06 02:46 UTC 时 10-05 在全部 interval 仍 404，最新只有 10-04 | 实测 curl（设计 §2.4 记录） |
| 3 | 旧游标 `cursor_upto = 窗口上界` ⇒ 与上条叠加＝**该日永不重试**（静默数据洞，`sync_log` 还记 success） | `engine.py`（旧 `_sync_binance_perp_daily`） |
| 4 | dev `binance_perp_daily` 的 `last_sync_date=NULL / last_status=running` ＝ 增量路径从未成功过的痕迹 | dev 库实测 |
| 5 | prod 磁盘 `/dev/vda3` 40G，**可用 8.3G（78%）**；1min ≈ **450MB/天**、只增不删 ⇒ 日调度约 **18 天打满** | ansible `df` 实测 |
| 6 | `bar_1h`/`bar_1min`/`bar_15min` 三表已存在（迁移 0064），`ensure_table` 认 `bar_{freq.lower()}` | `db.py::save_bars` |

## 依赖（就绪）

- 批 101 日线已上产（`202610060645-15ada91`）；`bar_1d` crypto 无洞。
- **未就绪（非阻塞）**：扩盘（vda3 → 100G+）——已从「前置项」降级为「放宽 `start_floor` 前置项」。

## 引用（设计 / 方案）

- `flow/方案/多市场数据接入与代理体系-设计-20261006.md` **§二** —— 存储闸门（§2.1）、interval 泛化（§2.2）、
  handler 工厂（§2.3）、游标契约修正（§2.4）、DataKind 收窄（§2.5）、迁移与验收（§2.6）。
- `docs/architecture/A06-部署管道架构.md` —— 迁移随 release 走 wrapper 的机制（本批不触发特殊路径）。

## 产出

| 面 | 文件 | 说明 |
|---|---|---|
| adapter | `server/src/data_platform/adapters/binance_adapter.py` | `_pull(sym,interval,…)` 抽出；`pull_daily` 委托；`pull_minute` 真实现；`_INTERVAL_BY_FREQ` 为 interval 映射**单源**；`capabilities` +3、`capability_decls` +`bar_minute` |
| engine | `server/src/data_sync/engine.py` | `_BINANCE_BAR_SPECS`（四 sync_id 一张表）+ `_make_binance_bar_handler` 工厂；`_crypto_window(…, first_run_days)`；`_HANDLERS` 20→**23** |
| 迁移 | `server/migrations/versions/0134_binance_perp_minute.py`（新） | `sync_config` 三行（`schedule='manual'`） + `sync_kind_config` 三行 |
| 映射 | `server/src/quant_common/markets.py` | `SYNC_ID_CAP_MAP` +3（`hist_quote`） |
| 测试 | `server/tests/test_batch101b_binance_perp_minute.py`（新，40 例） | 见 §验收标准 |
| 邻接 | `test_batch101_*`、`test_batch99_*`、`test_sync_config_coverage.py` | 三处计数/断言同步 |

## 限定范围

- **只改**：上述六处 + `scripts/test-migration-roundtrip.sh`（新增 case_o/case_p）+ `flow/` 文档。
- **明确不碰**：① 币安实时腿（`fapi.binance.com` 仍阻断，与代理无关）；② OKX（属 102b）；
  ③ `sub_kind='perp'` 立法与 `MARKET_OP_DECOMP`；④ 前端（本批零前端改动，已实证 `manual` 走
  `cronToModel` 的 raw 分支不崩）；⑤ 扩盘（运维决策）。

## 接口契约

1. **`_BINANCE_BAR_SPECS`**（唯一真源；`kind/sub_kind` 供 `fetch_supply` 分派，`freq` 同时决定
   表名 `bar_{freq.lower()}` 与 adapter 侧 interval）：

   | sync_id | kind | sub_kind | freq | 表 | 首跑窗口 |
   |---|---|---|---|---|---|
   | `binance_perp_daily` | bar_daily | perp | `1D` | `bar_1d` | 30 天 |
   | `binance_perp_hourly` | bar_minute | perp | `1h` | `bar_1h` | 7 天 |
   | `binance_perp_1min` | bar_minute | perp | `1min` | `bar_1min` | 1 天 |
   | `binance_perp_15min` | bar_minute | perp | `15min` | `bar_15min` | 1 天 |

2. **`_INTERVAL_BY_FREQ = {1D→1d, 1h→1h, 1min→1m, 15min→15m}`**（adapter 内 interval 单源）。
   `pull_minute(symbol, freq, start, end)` 只认内部 freq；源 interval 名（`15m`）与日粒度**响亮
   `UnsupportedFeature`**——放行会把调用方 bug 变成 `bar_15m` 脏表。
3. **游标**：`cursor_upto = 实际取到数据的最后一日`（多标的取 max）；全部无数据 ⇒ `start - 1 天`
   （＝旧游标，绝不空转、绝不跳日）。**不是**窗口上界（见设计 §2.4）。
4. **0 行两态**：整窗落在未发布尾区（`UTC今日 - start ≤ 2 天`）⇒ `log_status='skipped'`（正常等待，
   不进 failed）；窗口更长却 0 行 ⇒ `failed_dates += 'no_rows:…'`（真异常）。
5. **写入语义**：增量 `save_bars`（ON CONFLICT DO NOTHING）、回补 `save_bars_overwrite`
   （OHLCV 覆盖 + `adj_factor` COALESCE 保住）。
6. **`schedule='manual'` 是存储闸门**（设计 §2.1），改 cron 前必须扩盘 + 放宽 `start_floor`。

## 验收标准

```bash
cd server && venv/bin/ruff check <改动文件…>              # 清（曾抓出 0134 一个无用的 freq= 变量）
cd server && venv/bin/python -m pytest -q                # ✅ 2039 passed / 1 skipped（基线 1999 + 本批 40）
bash scripts/test-migration-roundtrip.sh                 # ✅ 含新 case_o（0134 值级+相对下界+幂等+降级）与 case_p（离线渲染）
```

邻接修正（均已绿）：`test_batch99` 回补能力 17→20（+`start_floor` 非空集合）；`test_sync_config_coverage`
handler 20→23 / 行数 26→29；`test_batch101` 三处（capabilities 集合、`pull_minute` 占位钉反转为
「真实现诚实性钉」、首跑窗口 30 天含端点）。

## mock 方式

- interval 严格性：直接调 `pull_minute(freq=…)`（不触网），断言 `UnsupportedFeature`。
- 工厂游标：`patch` 批量站返回固定 ts 集合，断言 `cursor_upto` = 最大日（含「含未发布日」的反例）。
- 真库对账：`TestDbReconciliation` 读 dev 库 `sync_config`/`sync_kind_config` ↔ `_BINANCE_BAR_SPECS` 逐项一致。

## 附 · 实证与修掉的真问题

**实证**（dev 真库 / 真批量站 / 真 handler）：

- 形状：`1h`=24 根、`15min`=96 根、`1min`=1440 根、`1D`=1 根（同一日）；`1D` 首根 open 与 `1h` 首根
  open 一致（84710.50，跨 interval 自洽）。
- **hourly 全宇宙 1004 标的**（7 天窗）：852s → `pulled=saved=131553`、`failed_dates=[]`、
  `cursor_upto=20261004`；`bar_1h` 13.2 万行 / 33MB，`ts ∈ [09-29 00:00, 10-04 23:00]`。
- **15min 回补**（前 40 标的 × 2 已发布日）：24.1s → `pulled=saved=6912`；抽样 `0GUSDT.BINANCE` 恰 192 行（2×96）。
- **1min 回补**（前 30 标的 × 1 已发布日）：38.6s → `pulled=saved=38880`；抽样恰 1440 行（10-04 00:00→23:59）。
- 游标实证：窗口含未发布的 10-05 时，三表实际最大 ts 均落最后已发布时刻 ⇒ 游标 20261004（不跳日）。
- **1min/15min 首跑（1 天窗）实测得 `skipped`**：该窗＝`[UTC昨日, UTC昨日]` 整段未发布 ⇒ 0 行、非失败
  （设计 §2.1 已记录；次日自愈或走 UI 回补）。

**修掉的两个真问题**：

1. **乐观游标吃日成洞**（批 101 遗留）：见 §现状 2/3/4 的因果链。
2. **无界增长 vs 已批准的有界档位**：原设计给日调度却只算单轮占用 ⇒ 见 §现状 5。落 `schedule='manual'`。

**挂账**（不属于本批）：

- `start_floor` 只在 **UI** 生效（`backfillDisabledDate`），后端不校验 ⇒ 直接调 API 可越界。30 个同步项
  同此现状，非本批引入；统一收口另立（已入 `flow/待办.md`）。
- `databus._FREQ_BY_KIND['bar_minute']='1min'` 的降级推断对 1h 是错的——该路径只在实时订阅暖机可达，
  加密无实时腿 ⇒ 挂到实时批（设计 §2.5）。
