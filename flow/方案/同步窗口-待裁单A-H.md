# 同步窗口与参数分层 · 待裁单 A–H

> 用途：**步 2 已过门（设计稿 v3），A–H 未裁定前不进步 3。** 本单逐点摊开「选项 × 具体形态 × 牵连面（代价）× 架构判据 × 三方结论」，供威廉姆直接拍。
> 判据（威廉姆裁定）：**架构合理性 > 实施成本**。故「代价」栏只记**牵连面 / 爆炸半径**（改了会碰到谁），**不作为取舍依据**；取舍一律写在「决定性判据」。
> 证据：`file:line` 或 dev 真库实测；设计稿 = `flow/方案/同步窗口与参数分层-设计.md` §十。
> 三方结论：**稿** = 设计稿建议；**A2 / B2** = 复审两名独立审稿人（A2/B2 只审了 A–G，**H 为本轮新增、未经复审**）。

---

## A. crypto perp 生命周期来源

**问题**：crypto 永续的「上币日（inception）」这一事实，落在哪张表？

| 选项 | 具体形态 | 牵连面 / 代价 | 架构判据 |
|---|---|---|---|
| ①新建独立 perp 生命周期表 | 新表装 vt_symbol/上币日/合约面值 | 与 SM 并存 ⇒ 两个「标的元数据」表 | **双真源**（前科：批 107 `etf_list.pg_table` 漂移） |
| ②扩 `security_master`（`market='crypto'`） | crypto 行进 SM，`list_date`=上币日 | 见下「连带」四件 | **单一真源**：SM 已是唯一跨市场主档 |
| ③推导规则（从 bar 表 `min(ts)` 推） | 不存事实，算出来 | 无 | 对**整窗 0 行**标的**天生失明**（OKX 7/485 形状）⇒ 排除 |

**②的连带（必须同批做）**：
1. **接填充链**：`_make_crypto_bar_handler`（`engine.py:1057`）现**零** `_sm_upsert` 调用；A 股是 `astock_list`/`cb_basic`/`etf_list` 三处（`engine.py:643/677/716`）。
2. **写侧补 `session_id`**：`SMClient.upsert_rows`（`security_master.py:161`）是 **12 列签名，不含 `session_id`**；该列迁移 `0092` 定 `NOT NULL DEFAULT 'astock_main'` ⇒ crypto 行走现签名会**落进 A 股 session（语义污染）**。须扩签名 + 显式传 `session_id='crypto_247'`、`trade_phase='T+0'`、真实 `multiplier`/`tick_size`。
3. **inception 取真值**：OKX `listTime`（`okx_adapter.py:202-206` 现只取 `instId`、**丢弃 listTime**）；Binance `exchangeInfo.onboardDate`（fapi 被墙 ⇒ 走待裁 **F**）。
4. **🔴 隐藏连带（本轮新发现）——A 会悄悄改写 crypto 的权限判定语义**：`perms.py:99-107` 对「标的无档（`SMClient.get` 返 None）」是 **fail-closed 恒拒**。crypto 今天无 SM 行 ⇒ 权限判定**恒 False**；A 灌入 crypto 行后，判定改走 `attr.category=='perp'` 分支（`perms.py:118`）。**实测 `account_permission` 三账户 `allowed_categories={stock,etf,convertible,fund,reits}`（无 `perp`）⇒ 仍拒，无意外放行**；但语义从「无档拒」变为「品类未授权拒」，**等于激活了 perp 权限路径**（`mgmt.py:595 _VALID_CATEGORIES` 已含 `perp`、`factor.py:726 "perp"→"crypto"` 已预期）。⇒ **属有意激活，须在批内明写**，否则又是一次「灌元数据顺带改行为」。

**现有 SM 读取方**（确认新增 crypto 行不误伤）：`routing.py:258`（选源）/ `quality.py:23`（对账 `category='stock'`）/ `perms.py` / `factor.py:731`（品类）——crypto 行 `category='perp'`，不撞现有查询。

**三方**：稿=**有条件同意**（条件＝inception 取真值）｜A2=同意｜B2=同意。
**决定性判据**：单一真源。`security_master` 已是系统唯一跨市场主档（`vt_symbol` 主键、`_market_of_suffix` 已认 `.BINANCE`/`.OKX`、`market_hours` 已有 `crypto_247`、crypto 需要的 `multiplier`/`tick_size`/`session_id` 只有 SM 有列）⇒ **②**。

---

## B. `retention`（保留下限）的落地形态

**问题**：「我们关心/保留多早的数据」这个**策略**值，存哪、谁能覆写？

| 选项 | 具体形态 | 牵连面 / 代价 | 架构判据 |
|---|---|---|---|
| ①仅 kind 默认 | 代码里按 kind 给一个回看天数 | 表达不了「个别任务要更早/更晚」 | **同 kind 强绑**，违背「换任务才变 ⇒ DB」 |
| ②每任务列 + kind 默认 | `sync_config` 加 retention 列；列 NULL ⇒ 读入时合并 kind 默认 | 需新增「kind 默认」承载（见代价）；与 **H** 联动 | **判据「换任务才变 ⇒ DB」** ✓ |
| ③保持现状（混在 `start_floor`） | 不动 | `start_floor` 一列三义（见 **H**） | 契约不闭合 |

**②的牵连**：
- **「kind 默认」今天没有家**：`sync_kind_config`（38 行）列＝`sync_id,kind,sub_kind,pg_table,pk_cols,float_cols,text_cols,rebuild`，**是按 sync_id 组织的**，无「按 kind 的默认表」概念 ⇒ 须决定：新表，还是按 `kind` 列去重出一份默认。
- **封顶必须可见（B2 R1 / A2 P1-b，两代理共同指认）**：`retention > inception` 时，`[inception, retention)` 是**我们主动不保留**，**不是「不存在」**。⇒ 须按下界取 max 时**显式登记 `policy_discard`**（可见排除声明 + 理由），**禁静默吞掉**。否则「误种一个偏晚的 retention」＝**无声丢史**，且与「源头就没有」同形。
- **与 H 联动**：retention 正是从 `start_floor` 拆出来的那一支。

**三方**：稿=**任务列为准 + kind 默认作默认值来源**｜A2=同意｜B2=同意（附：须加「retention 压制 inception」校验）。
**决定性判据**：「换任务才变 ⇒ 定义层」。retention 逐任务不同 ⇒ 必须任务级可表达；kind 默认在**读入时合并**（列 NULL 才取默认），运行时只有一个有效值 ⇒ 不构成双真源。

---

## C. 对账调度频率

**问题**：per-symbol / per-date 对账多久跑一次？设一个全局值吗？

| 选项 | 具体形态 | 牵连面 / 代价 | 架构判据 |
|---|---|---|---|
| ①全局单一值 | 一个 cron 频率管所有族 | 又开一个「没有家」的全局常量 | **§一.2 的同构缺陷**（窗口策略没有家） |
| ②按族/kind 定义 | per-date 每轮 / per-symbol 低峰或周级 / 快照不对账 | 见下 | **职责层级**：不同族职责不同 |

**②的牵连**：
- **per-date：每轮**——「本轮声称完成的区间必须真实完整」是**同步器的固有职责**（主路径收尾）；判据 `trade_cal` 已存在、无前置依赖。
- **per-symbol：低峰 / 周级**——依赖 **A** 与第 1 步，属**旁路深度修复**。
- **前置**：**输出必须闭环**（§7.2：落 `sync_gap` ＋告警 ＋限频重拉），否则「周级报告」＝**静默**。
- **快照族：不对账**——无「补历史」概念。

**三方**：稿=**不设全局值，按族/kind 定义**｜A2=同意｜B2=同意。
**决定性判据**：若做全局单选，就再造一个「窗口策略没有家」的全局常量（本稿 §一.2 的病根）⇒ **②**。

---

## D. per-date 族对账优先级

**问题**：日期级对账（`astock_daily`/`etf_daily`/`cb_daily`/TIER1 族）与标的级对账，谁先做？

| 选项 | 具体形态 | 牵连面 / 代价 | 架构判据 |
|---|---|---|---|
| ①与 per-symbol 同批 | 一起上 | 被 per-symbol 的依赖（A/第 1 步）**拖住** | 无独立价值 |
| ②先 per-date | 先落日期级差集 | 见下 | **可独立成立的最小完备子集** |

**②的牵连**：
- **依赖闭合度**：per-date 判据 `trade_cal` **已存在**、**不依赖 A / 第 1 步**；per-symbol 依赖 A 与第 1 步。
- **职责层级**：per-date 是**同步语义自洽**（主路径）；per-symbol 是**深度修复**（旁路）。
- **收敛方向**：per-date 对账**替代正在起作用的 `_TIER1_OVERLAP_DAYS=3`**（`engine.py:1548`）⇒ 使「启发式只减不增」，与 §六 收编方向一致。
- **非二选一**：两者治**不同类别**的洞（§七）——per-date 治「整日失败」（连接抖动丢一天），per-symbol 治「某标的整窗 0 行」。**是排序，不是取舍。**

**三方**：稿=**先 per-date**｜A2=同意｜B2=同意。
**决定性判据**：依赖闭合度 + 职责层级。per-date 是**能立即做且独立成立**的那个。

---

## E. 孤儿参数键闸门

**问题**：要不要立一条 CI 闸门，禁止「同步参数列声明了却没有执行面消费者」？

| 选项 | 具体形态 | 牵连面 / 代价 | 架构判据 |
|---|---|---|---|
| ①立（列级 + 带理由豁免 + CI 强制） | `sync_config` 策略/声明列须在 `src/**`（非测试）有读取点 | 见下 | **铁律一的可执行形式**（§三） |
| ②不立（仅启红提示） | 无 | 口号化 | 「声明必须被执行面消费」无强制 ⇒ §2.2 两列的历史重演 |

**①的牵连**：
- **首批断言对象**：`start_floor`（拆正名后 engine 必须**真读** retention）＋ `supports_backfill`（改为从 handler 派生后须有归宿：有消费者 / 删列 / 显式豁免+理由）。
- **现状**：两列在 `server/src`+`server/scripts`+`deploy`+`web/src` 共 **23 处**引用（含 docstring、`schema_expectations.txt:85`、`web_api/routes/sync.py:21` 前端读、`okx_adapter.py:240` 日志）。
- **历史 NULL 行**须**显式豁免 + 理由**（`start_floor` 30 行里 7 行非空、23 行 NULL）。
- **同族先例**：批 107 的对账门（注册表 ↔ 真源）。

**三方**：稿=**立**｜A2=同意｜B2=同意（**强**：防「声明≠执行」复发的唯一硬手段）。
**决定性判据**：没有它，铁律一只是口号；有 §2.2 两列作实证。⇒ **立**。

---

## F. 真上币日不可得时的表达

**问题**：Binance `onboardDate` 被墙、拿不到真上币日时，`security_master.list_date` 怎么写？

| 选项 | 具体形态 | 牵连面 / 代价 | 架构判据 |
|---|---|---|---|
| ①显式「未知」 | `list_date` 写 NULL + 显式标记 | 见下 | **语义不污染**（§九.3） |
| ②用可达性近似并标注 | 填 `first_available_month` 等探来的值 | 下游无法区分真值与近似 | **语义污染** |
| ③用月包 floor | 填 `2020-01` | 同上，且更粗 | 同上 |

**①的牵连**：
- **未知时的窗口行为**：对账按 `uncertain`（§5.4/§7.2），**抑制一切缺口主张**；`retention`/`source_earliest` 仍可作下界（它们与 inception 独立）。
- **须铰入输出契约（B2 R2）**：`list_date IS NULL` ⇒ 必走 `uncertain`、**不得进入 `closed` 判定**——机制有、**铰点缺**，须写进 §7.2。
- **先例（不是新规矩）**：迁移 `0136` **故意留 NULL**、`0137` 序言「只填**已实证**下界、其余 NULL，**不臆造**」⇒ 本待裁只是把这套既有纪律**从源下限推广到 inception**。

**三方**：稿=**显式「未知」**｜A2=同意（附条件：须抑制缺口主张）｜B2=同意。
**决定性判据**：单一真源字段的可信度是资产。语义污染一旦埋进主档，下游**无法区分「真上币日」与「源可达近似」**。

---

## G. `inception` 单一真源

**问题**：`inception` 有两个居住地——`security_master.list_date` 与 `asset_static_info`/`etf_basic_info`/`cb_basic_info`。哪个是写点、哪个是读点？

**关键事实（本轮坐实）**：三个列表同步**已经双写**——同一份 `df` 既 `INSERT INTO asset_static_info/etf_basic_info/cb_basic_info`，又调 `_sm_upsert` 写 SM（`engine.py:643/677/716`）。即：**一次拉取 fan-out 到两表**，不是两个独立源。**SM 已有活跃读取方**（`routing.py:258` 选源 / `quality.py:23` 对账 / `perms.py` 权限 / `factor.py:731` 品类）。

| 选项 | 具体形态 | 牵连面 / 代价 | 架构判据 |
|---|---|---|---|
| ①**SM 成真上游**：`_get_list_date` 改读 SM | 原表保留（供 SM 无的字段：行业/基金类型）；读取走 SM | `_get_list_date` 现 5 调用点（`engine.py:2126/2142/2151/2173/2360`）改读 SM | **单一可读点**，且 crypto 有家 ✓ |
| ②**SM 视为原表投影** | 保留原表为真源，SM 只是副本 | crypto **无原表** ⇒ ②对 crypto **不成立** ⇒ 必须给 crypto 例外 | **同表两种写权限**（astock 投影 / crypto 原始）⇒ 更乱 |

**①的牵连**：`_get_list_date`（`engine.py:1860`，现读三原表、回退 `_TUSHARE_MIN_DATE`）改读 SM；原表**不删**（仍装 SM 没有的列）。
**②的失败模式**：SM 对 astock 是「投影」、对 crypto 是「原始」——**同一张表两种性质**，读的人无法判断某行是不是权威。且 crypto 没有可投影的原表。

**三方**：稿=**择一并写死**｜A2=同意（**倾向①**：SM 成三表真上游，与 A 一致）｜B2=同意但（**倾向②**：SM.list_date 视为投影，不新增写路径）。
**决定性判据**：单一真源 = 同一事实只允许一个可写点。**crypto 的唯一家就是 SM** ⇒ ②会逼出 crypto 例外 ⇒ **①更闭合**。（B2 的②前提是「SM 没有写路径」——实测 `_sm_upsert` 已在写，其前提不成立。）

---

## H. `start_floor` 一列三义的正名（**本轮新增，未经复审**）

**问题**：`sync_config.start_floor` 这一列同时装了三种语义，怎么收拾？

**实测（DB，30 行中 7 行非空）——逐行归语义**：

| sync_id | 值 | 真语义 |
|---|---|---|
| `binance_perp_daily` | `2019-09-08` | **inception**（上币日，迁移 `0132` 自注 USDT-M 上线日） |
| `okx_perp_daily` | `2020-01-01` | **源下限**（迁移 `0137` 明写「实测 K 线边界」） |
| `binance_perp_hourly` | `2026-09-29` | **retention** |
| `binance_perp_15min` / `1min` | `2026-10-05` | **retention** |
| `astock_basic` | `1990-12-19` | 无 inception 族（上交所开业日） |
| `index_daily` | `2005-04-08` | 无 inception 族（§九.7 硬编码） |

⇒ **同 provider 内部就三种语义**，无法用规则自动判。

| 选项 | 具体形态 | 牵连面 / 代价 | 架构判据 |
|---|---|---|---|
| ①**拆**：retention 留 `sync_config`（引擎真读）＋ 源界归 `available_range` ＋ inception 归 SM | 三语义三处 | 见下 | **契约闭合**（每个读取方拿到即懂语义） |
| ②保留单列 + 加语义标列 | 再加一列标语义 | 软约定 | **软约定＝没约定**，判据不可 CI 化 |
| ③不改 | 不动 | 任何读取方都得先猜 | 不可判定 |

**①的牵连**：
- **逐行判的量很小**：**只有 7 行**要判语义（30 行里）。
- **读取方重定向**：engine 真读 retention（新列）；`_get_list_date` 走 SM（**G**）；源界收进 `adapter.available_range`（**第 1 步**）。
- **列形状门**：`schema_expectations.txt:85` 须更新（sync_config 列清单）。
- **迁移**：加 retention 列 / 停 `start_floor` 列 ⇒ **破坏性迁移两步走**（本版 expand 下版 drop）。
- **前端**：`web_api/routes/sync.py:21` 的 SELECT ＋ `DataManage.vue:467 backfillDisabledDate` 须改。
- **与 E 联动**：拆完后 E 闸门断言 engine 真读 retention。

**三方**：稿=**拆**｜A2/B2 **未审**（本轮新增）。旁证：A2 的 P1-b 修法②「给 `start_floor` 加迁移期来源审计护栏」与拆法**同向**；B2 R1 亦要求加 guard。
**决定性判据**：一列三义下**任何读取方都必须先猜语义**——「读到的值是什么」不可判定 ⇒ 契约不闭合。先例＝批 107 `etf_list.pg_table` 漂移。⇒ **①拆**。

---

## 汇总表（一屏看完）

| # | 待裁 | 稿建议 | A2 | B2 | 助手推荐 | 决定性判据 |
|---|---|---|---|---|---|---|
| A | crypto 生命周期来源 | 扩 SM（有条件） | 同意 | 同意 | **扩 SM**（＋perms 连带） | 单一真源 |
| B | retention 落地形态 | 任务列 + kind 默认 | 同意 | 同意 | **同上** | 换任务才变 ⇒ DB |
| C | 对账调度频率 | 不设全局，按族/kind | 同意 | 同意 | **同上** | 避免「没有家」的全局常量 |
| D | per-date 优先级 | 先 per-date | 同意 | 同意 | **同上**（非二选一） | 依赖闭合度 |
| E | 孤儿键闸门 | 立（CI 强制） | 同意 | 同意（强） | **立** | 铁律一的可执行形式 |
| F | 上币日不可得 | 显式「未知」 | 同意（附条件） | 同意 | **同上**（＋铰入 §7.2） | 语义不污染 |
| G | inception 单一真源 | 择一写死 | 同意（倾向①） | 同意但（倾向②） | **①**（SM 为真上游） | crypto 无原表 ⇒ ②必出例外 |
| H | `start_floor` 三义 | 拆 | 未审 | 未审 | **①拆** | 契约闭合（先例批 107） |

**上表 8 条全部只有「一个架构上站得住的选项」**——即 A–H **无真正两难**，分歧仅在 **G 的方向**（A2①/B2②）。我的判定＝**①**，理由见 G（B2 的前提「SM 无写路径」经实测不成立）。
