# 盲审 · 同步窗口与参数分层设计 · 代理 B

> 评审对象：flow/方案/同步窗口与参数分层-设计.md
> 基线：55dea79
> 一句话总评：窗口三层归属与启发式收编方向正确，但 §2.6/§八.2 把 Binance `first_available_month`（数据可得性）误当 crypto `inception`（上币日），并模型缺「源最早可得（下界）」边界——按稿实施会在 OKX-7/485 那类洞上换形态重开，须返工。

## 致命（P0）
- **P0-1 inception 语义错配 + 缺源下界边界**：`binance_adapter.first_available_month(sym)`（binance_adapter.py:215-236）是**二分探测该符号「最早可得月包」**（`_month_ok` 探 kline 是否返回；`_MONTHLY_FROM=(2020,1)` 全局 floor，注释"2019-09/10→404"）＝**源最早可得月份，非上币日**；设计 §2.6/§八.2 却要「把 Binance 月包下限 + OKX listTime 解析成 list_date（inception）」。→ 对 0 行永续，`first_available_month` 返回 None ⇒ inception=None ⇒ per-symbol 对账「期望集为空」仍失明，**正是 OKX-7/485 的原型**；且模型 `窗口=[max(inception,retention), source_upper−publish_lag]` 只有上界源界、缺下界「源最早可得」，适配器内部（binance_adapter.py:255-267）私自用 first_available_month 收窄起点，使「模型窗口≠真实窗口」、对账层无法信任。→ 修法：inception 只能取自真上币日（OKX `listTime` / Binance exchangeInfo 上币日，非 kline 月包）；模型补第四条下界 `max(inception, retention, source_earliest)`，其中 `source_earliest` 由 `available_range(kind)` 一并返回。

## 严重（P1）
- **P1-1 inception 依赖倒置未闭合**：§九.1 自认「列表快照过期⇒时序族静默漏报，应判『不确定』而非『无缺』」，但 §5/§八 实施序无任何机制把 SM 新鲜度/staleness 转成对账的「不确定」输出。→ 按稿实施后 stale 列表仍静默漏报。→ 修法：在 per-symbol/per-date 对账输出里加 `stale_source` 状态，列表快照 age 超阈即标「不确定」、不报无缺。
- **P1-2 换源回退仍可能「吃掉」窗口**：§九.3 承认 `_get_list_date` 硬编码 `_TUSHARE_MIN_DATE`（=20100101）当 tushare 回退，换源即错，须 step1 先落 `available_range`。但 §八.1 列举源界只点 tushare/binance/okx/分页触顶，**漏 joinquant `account_window()`（engine.py:1202 起，第三处起点计算）**——若 `available_range` 不覆盖 joinquant，换源 bug 残留在第四类源。→ 修法：step1 显式纳入 joinquant 账户区间。
- **P1-3 删除冻结门控前提难证**：§八.5 删除 `freeze_on_partial` 门控于「①②③④落地且对账在 prod 证明覆盖同一缺口」。但 OKX-7/485 天然低频（485 里 7 个），prod 验证需长周期；过渡期并存虽「无正确性损失」，门控释放时点无客观判据 ⇒ 易过早删导致洞重开。→ 修法：门控释放加可观测指标（连续 N 轮 per-symbol 对账命中同缺口 0 误报）而非时间。
- **P1-4 E 闸门与 start_floor/supports_backfill 自身归宿未闭合**：设计 §2.2 承认两列「执行面零引用」、§四把 start_floor 升格为执行参数、supports_backfill 「改从 handler 派生」。但 OKX `start_floor` 历史 NULL（0136 留空、0137 才回填 2020-01-01），E 闸门要求「声明必须被执行面消费」——升格后 engine 必须真读，且 NULL 行须明确豁免+理由，否则成新孤儿。→ 修法：E 闸门落地时把这两列列入首批断言对象。

## 一般 / 陷阱核对
- §2.1「窗口起点只有 `_sync_via_kind`+`_crypto_window` 两处」**漏 joinquant `account_window()`**（engine.py:1202，第三处起点计算）。模型须覆盖 ≥3 起点站点。
- 「crypto_perp_daily 是孤儿调度」**过时**：该名 2026-10-06 改名 `binance_perp_daily`（迁移 0132，flow/进展/2026-W40.md:221/253），`data_increment_crypto` 死构件已删（test_batch101:594 `hasattr` false）；现真实调度 `binance_perp_daily(30 8 * * *)` / `okx_perp_daily(40 8 * * *)`。crypto 生命周期仍空（SM 零 crypto 行）属实，但「孤儿调度」前提错。
- `_PARTIAL_FREEZE_MAX_DAYS=10` 仅挂 `binance_perp_daily`(1177)/`okx_perp_daily`(1194) 日线档，分钟/小时档不冻结（G-S1）——与 §2.3 表一致，属实。
- §九 已列 5 风险+1 不做，但**未含 P0-1 的「源下界缺失/inception 错配」**，属风险清单重大遗漏。
- 与 A03/A04：三层归属（定义/能力/接入）与供给总线「capability 收编=批107方向」意图一致，无硬冲突；但 `available_range` 放 adapter 接入层须与 A04 adapter 契约对齐，稿未引 A04 具体条。

## 简化机会
- 模型本质是「四维交集」（inception / retention / source_earliest / source_latest−lag）。可把 §5.1 两张「上界源界」与缺失的「下界源界」合并为单个 `available_range()→(earliest,latest)` 返回，避免上/下界分置两处、减少日后再次漂移。

## 事实核查结果
| 设计稿断言 | 核实结论 | 证据 |
|---|---|---|
| §2.1 起点只有 `_sync_via_kind`+`_crypto_window` 两处 | 不符（漏 joinquant `account_window`） | engine.py:1202 起；§2.1 表自身列了 joinquant |
| `20050408` 硬编码 index_daily | 符合 | engine.py:1520（在 `_sync_via_kind` 分支） |
| `start_floor`/`supports_backfill` 执行面零引用 | 部分符合 | engine `_get_config` SELECT(219) 不含两列；全仓无 `cfg["start_floor"]` 读取；但 `web_api/routes/sync.py:21` SELECT 仅序列化 UI、`schema_expectations.txt:85` 含两列。engine/backfill 路径「不读」成立 |
| 四条启发式常量位置/语义/触发者 | 符合 | `_PARTIAL_FREEZE_MAX_DAYS=10`(1054) 挂 binance/okx perp daily(1177/1194)；`_TIER1_OVERLAP_DAYS=3`(1548→1595)；`_CRYPTO_TAIL_GRACE=2`(1049→1154)；`_TIER1_LAG_TRADING_DAYS`(1552→1778) |
| SM 9710/分品类/crypto 零行 | 符合 | DB 实测：market=astock 全 9710；stock 5572/etf 2966/convertible 1172；list_date 9515/9710 |
| `_sm_upsert` 仅 3 处 + crypto bar handler 零调用 | 符合 | engine.py:643/677/716（astock_list/cb_basic/etf_list）；`_make_crypto_bar_handler` 无调用 |
| `upsert_rows` 12 列缺 `session_id` | 符合 | security_master.py:177-179 INSERT 12 列无 session_id；迁移 0092:50 `session_id NOT NULL DEFAULT 'astock_main'` ⇒ crypto 落 astock session |
| OKX `listTime` 被丢弃 | 符合 | okx_adapter.py:202-206 仅取 instId + 过滤 settleCcy，无 listTime 提取 |
| **Binance `first_available_month` 语义** | **= 源最早可得月份（非上币日）** | binance_adapter.py:215-236 二分探 `_month_ok`（kline 月包是否返回）；`_MONTHLY_FROM=(2020,1)` 注释"2019-09/10→404" |
| crypto 落共享 bar 表 + `source` 列 | 符合 | bar_1d source=binance 4631 行（%.BINANCE 4631）；bar_1h 131553/bar_15min 8640/bar_1min 38880 均 binance/okx；非独立表 |
| 「crypto_perp_daily 是孤儿调度」 | 不符（过时） | 改名 binance_perp_daily（0132）；`data_increment_crypto` 已删（test_batch101:594） |

## 对待裁点 A–E 的独立结论
| # | 设计稿建议 | 你的结论 | 理由 |
|---|---|---|---|
| A | 扩 `security_master` 载 crypto 生命周期 | 同意 | 单一真源（vt_symbol 主键、`_market_of_suffix` 认 crypto、`covers()`「未回填≠不可用」已预期）；形状匹配（multiplier/tick_size/session_id 仅 SM 有列）。**须连带**：upsert_rows 补 session_id 通道 + inception 取自真上币日（非 first_available_month，见 P0-1） |
| B | retention：任务列 + kind 默认 | 同意 | 换任务才变⇒DB；值已存在。但现有 binance `start_floor=2019-09-08` 实为「上线日/源下限」非 retention，落地须正名分离；OKX `start_floor` 曾 NULL→0137 回填 2020-01-01，作 retention 会与早于该日上市 perp 冲突（同 P0-1 病灶） |
| C | 对账频率不设全局、按族定义 | 同意 | 全局单选＝又开一个「无家」全局常量（§一.2 同构缺陷）；per-date 每轮属主路径收尾、per-symbol 低频，论证成立 |
| D | per-date 先 | 同意 | 依赖闭合度论证成立（per-date 用 trade_cal、不依赖 A/step1）；先替换在跑的 `_TIER1_OVERLAP_DAYS` 使启发式只减不增，方向一致 |
| E | 孤儿参数键闸门：立 | 有条件同意 | 是「声明必须被执行面消费」铁律的可执行形式（§2.2 两列即实证）。**条件**：须 CI 强制（非仅启红）+ 豁免带理由；且 `start_floor`/`supports_backfill` 自身须在闸门下明确归宿（升格或派生），否则成新孤儿（见 P1-4） |

## 过门判定
- 是否存在 P0：是（P0-1）
- 是否建议通过步 2：否 / 有条件——条件 = 先修 P0-1（① inception 改取自真上币日、禁把 `first_available_month` 当 inception；② 模型补「源最早可得」下界边界 `available_range()→(earliest,latest)` 双返回）；P1-1/P1-2 须在实施序落地前补解法，否则 stale 列表与 joinquant 换源仍静默漏报。
