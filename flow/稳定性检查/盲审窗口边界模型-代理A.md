# 盲审 · 同步窗口与参数分层设计 · 代理 A

> 评审对象：flow/方案/同步窗口与参数分层-设计.md
> 基线：55dea79
> 一句话总评：三边界窗口模型与三层归属方向正确、与 A03/批107 一致；但 §八 第 2 步的 crypto inception「来源机制」对 Binance 用错了信号（月包 floor≠上币日），会在老永续上静默丢 2019H2 全量历史（P0）；另有多处模型自洽性与契约闭合缺口（P1）。

## 致命（P0）
- **P0-1 Binance inception 来源机制错误→静默丢史**：证据（binance_adapter.py:49 `_MONTHLY_FROM=(2020,1)` 注释「月包自 2020-01（2019-09/10→404）」；:215-236 `first_available_month` 探测的是**月包**可达性；:246-249 注释明写日包更早、用月包起点当起点会「静默丢掉 2019-09~2019-12 逐日历史」）→ 设计 §八 第 2 步「把 Binance 月包下限解析成 list_date」会把所有 2019 上市的 USDT-M 永续 inception 钉成 2020-01 → 窗口 `[max(inception,retention),…]`（retention 同为 2019-09-08 时 max=2020-01）→ **静默丢弃全市场 2019-09~2019-12 日线（源确有，注释自证）** → 修法：Binance 须取真实上币日（exchangeInfo.onboardDate 或代码已知日包下界 2019-09-08），绝不可用 `first_available_month`。

## 严重（P1）
- **P1-1 期望集与窗口下界不一致（retention>inception 时自相矛盾）**：证据（§5.1 窗口=`[max(inception,retention),…]`；§七 per-symbol 期望集=`[inception,end−lag]`；DB `okx_perp_daily.start_floor=2020-01-01`）→ 当 retention(2020-01-01) > 某 perp 真实 inception(2018/2019 上市) 时，拉取起点=2020-01、期望集含 2018/2019 → 永久缺口告警或（对账认亏）静默丢史、循环不收敛 → 修法：期望集下界必须等于窗口下界 `max(inception,retention)`，retention 同时封顶「期望」。
- **P1-2 窗口下界缺 source_earliest 项**：证据（§八 第 1 步 `available_range→(earliest,latest)` 仅把 latest 当上界；binance_adapter.py:215 证明 earliest 真实存在且常晚于 inception）→ `max(inception,retention)` 可能请求源给不到的早于 source_earliest 的数据→空拉/伪缺口 → 修法：下界=`max(inception, retention, source_earliest)`。
- **P1-3 对账输出闭环未定义**：证据（§七「per-symbol=旁路深度修复」、§C「低峰/周级」，全文无「缺口去哪/自动重拉 or 告警」消费者）→ 若对账只产报告无人消费＝静默，等于没对账 → 修法：明写 per-date 内联重拉（主路径）、per-symbol 落 gap 表+告警+限频重拉。
- **P1-4 删冻结后的检测时延缺口**：证据（engine.py:1147-1151 `freeze_on_partial` 每轮拦截；§八 删除门）→ 删冻结后 crypto 0 行/单标的逐轮失败改由周级 per-symbol 对账兜底，存在≤1 周静默窗（恰是 OKX 7/485 那类）→ 修法：门控判定须含「per-symbol 对账已调度且自动重拉」，而非仅「prod 证明覆盖」。
- **P1-5 available_range 须为硬契约**：证据（§八 第 1 步未要求「每个可拉 adapter 必实现、返回 None 即 fail-loud」）→ 新源漏实现默认 today→永不回补历史，source_upper 成软真源 → 修法：CI 断言 pull-capable adapter 必实现 available_range，None 抛错。
- **P1-6 §七 分族表漏列 tier1 族 + §2.6 月包表述错**：证据（engine.py:1548/1552/1587/1778 `_TIER1_*` 自算窗口；§七表只列 astock/etf/cb_daily）与（binance_adapter.py:49 月包 floor=2020-01，设计 §2.6 称 2019-09-08 为「UM 月包最早月」≠代码）→ tier1 族窗口逻辑可能漏收编；§2.6 把实际上市/日包下界误标月包下界 → 修法：§七补 tier1 行；§2.6 改「UM 上线日(日包下界)」。

## 一般 / 陷阱核对
- `supports_backfill` 改「从 handler 派生」：E 闸门下须给该列明确归宿（engine 读 or 显式豁免+淘汰），否则成新孤儿。
- `_get_list_date` 现读 `asset_static_info/etf_basic_info/cb_basic_info`（engine.py:1862），**非** security_master；设计「inception 来自 SM」是迁移方向，需保证 SM.list_date 是这些表真上游或改 `_get_list_date` 读 SM，否则 inception 仍双源。
- `index_daily` 无列表来源（仅硬编码 `20050408`，engine.py:1520）；换模型后需补 index 生命周期或显式「无 inception 则仅靠 retention」。

## 简化机会（可选）
- §2.1 标「窗口起点仅两处」易误导：实际另有 `_sync_astock_daily_jq`(account_window, joinquant_adapter.py:166) + `_make_tier1_handler`(自算) 各自算起点。建议在 §一/§二 显式点名全部起点来源，避免实施漏改。

## 事实核查结果
| 设计稿断言 | 核实结论 | 证据 |
|---|---|---|
| §2.1 `index_daily` 硬编码 `20050408` | 符合 | engine.py:1520 |
| §2.1 窗口起点仅 `_sync_via_kind`+`_crypto_window` | 部分不符（遗漏） | 另有 `_sync_astock_daily_jq`(joinquant_adapter.py:166)+`_make_tier1_handler`(engine.py:1587) 自算；§2.1 自列 4 行但 §七未覆盖 tier1 |
| §2.2 `start_floor`/`supports_backfill` 执行面零引用 | 符合 | engine._get_config(217) SELECT 不含两列；仅 web_api/routes/sync.py:21/27 渲染 |
| §2.3 `_PARTIAL_FREEZE_MAX_DAYS=10` 仅挂 binance/okx_perp_daily | 符合 | engine.py:1054；`freeze_on_partial=True` 仅 1177/1194 |
| §2.3 `_TIER1_OVERLAP_DAYS=3`/`_TIER1_LAG_TRADING_DAYS` | 符合 | engine.py:1548/1552/1595/1778 |
| §2.3 `_CRYPTO_TAIL_GRACE=2` | 符合 | engine.py:1049/1154 |
| §2.4 游标 sync_id 级 vs 失败 symbol 级 | 符合 | engine.py:1147-1151 整窗重拉 |
| §2.5 security_master 9710 / 5572·2966·1172 | 符合 | DB 实测 |
| §2.5 `_get_list_date` 5 处调用，仅 sync_symbol/pool_data | 符合 | 调用点 2126/2142/2151/2173/2360；`_find_gaps` 仅 2129/2157 |
| §2.5 缺口扫描 `_find_gaps` 仅 sync_symbol | 符合 | grep 仅 engine 内 2 处，均在 sync_symbol |
| §2.6 `_sm_upsert` 全仓仅 3 处，crypto handler 零调用 | 符合 | engine.py:643/677/716；`_make_crypto_bar_handler`(:1057-1170) 无调用 |
| §2.6 SM 已容纳 crypto（_market_of_suffix / covers 注释） | 符合 | security_master.py:152-159,121-149 |
| §2.6 节奏域 `crypto_247` 已备 | 符合 | 迁移 0092:91 种子 |
| §2.6 OKX `listTime` 在响应内但被丢弃 | 部分符合 | 代码仅取 `instId`+`settleCcy`(okx_adapter.py:204-207)，确丢弃；「响应含 listTime」为外部 API 事实，代码无法自证（记为外部未证） |
| §2.6 crypto 落共享 bar 表 + source 列区分 | 符合 | DB: binance/okx_perp_daily.pg_table='bar_1D'；bar_1d 含 `source` 列 |
| §2.6 族级 `start_floor` 已填=源下限（binance 2019-09-08 / okx 2020-01-01） | 值符合；标号错 | DB 实测值对；但 binance_adapter.py:49 月包 floor=2020-01、2019-09-08 是**上市/日包下界非月包**→「UM 月包最早月」表述与代码不符 |
| §2.6 SM 写侧缺 `session_id`（12 列签名） | 符合 | security_master.py:161-187（12 列无 session_id）；迁移 0092:50 `session_id NOT NULL DEFAULT 'astock_main'` |
| §2.6 crypto 在 security_master 零行 | 符合 | DB: market='crypto' 0 行 |
| 「crypto_perp_daily 是孤儿调度」 | 不符（不实） | 全仓无 `crypto_perp_daily` 残留；该名已于 2026-10-06 经裁决改名 `binance_perp_daily`（迁移 0132，flow/进展/2026-W40.md:221/253）。真实已调度：binance_perp_daily(30 8 * * *)/okx_perp_daily(40 8 * * *) |

## 对待裁点 A–E 的独立结论
| # | 设计稿建议 | 你的结论 | 理由 |
|---|---|---|---|
| A | crypto 生命周期来源：扩 security_master | **有条件同意** | 单一真源方向对（新建表=第二真源，批107 etf_list.pg_table 漂移先例）；「推导(min ts)」正确排除（0 行标的失明=OKX 7/485 形状）。但 Binance 侧**不可用 `first_available_month` 当 list_date**（见 P0-1），须取真实上币日；OKX 用 `listTime`（代码已确认丢弃，需接入）。 |
| B | retention：任务列+kind 默认 | **同意** | 换任务才变⇒DB，仅 kind 默认表达不了差异，判据成立；值已存在。但现有 OKX `start_floor=2020-01-01` 作 retention 会与早于该日上市的 perp 冲突（见 P1-1）；须显式 separation：retention 与 source_earliest 语义分离、期望集同步用 `max(inception,retention)`。 |
| C | 对账频率：不设全局值、按族/kind | **同意** | 全局单选＝又一处「无家」全局常量（§一.2 同构缺陷）。补：per-date 每轮内联（主路径收尾）、per-symbol 必须调度且输出闭环（见 P1-3）。 |
| D | per-date 对账优先级：先 per-date | **同意** | 依赖闭合度判断正确（per-date 不依赖 A/step1，且替代在跑的 `_TIER1_OVERLAP_DAYS`）；非二选一、纯排序，合理。 |
| E | 孤儿参数键闸门：立（列级+带理由豁免） | **同意** | 是「声明必须被执行面消费」铁律的可执行形式（§2.2 两列即实证）。补：须 **CI 强制**（非仅启红），豁免带理由；`start_floor`/`supports_backfill` 须在此闸门下明确归宿。 |

## 过门判定
- 是否存在 P0：是（P0-1）
- 是否建议通过步 2：有条件——必须修正 P0-1（Binance inception 来源，否则 crypto 历史系统性静默缺失＝返工级）；并在实施规格中闭合 P1-1~P1-5（期望集下界一致、available_range 硬契约、对账输出闭环、删冻结门控含自动重拉）。P0-1 不修则步 2 不予通过。
