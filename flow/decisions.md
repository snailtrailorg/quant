# 决策日志 (decisions)

> 只记**仍然有效的架构决策**（为什么这样做）。已推翻的已清理；过程考古在 git log。
> 新决策追加在最上面。

---

## 2026-10-08 · 批 111 步 4 **复审轮**两条上调（「实现 ∉ 已批准规范」＝纯 bug 修复；**约定**不构成降级理由）

**背景**：批 111 步 4 复审轮（同判 `flow/稳定性检查/盲审迁移两步走立法-步4代码-复审-同判②.md`）：两名复审员**双同「无 P0」**，主会话按**已批准判据**上调 **2 P0**。

**决策 1 —— 「实现 ∉ 已批准规范」的修复属**纯 bug 修复**，不新增设计裁定，故不需用户重新拍板**
- 事实：① 规范 §5.1 原文 keying ＝「**名在冻结集内** ⇒ 不阻断，但另比 `upgrade` 段逐字一致」，实现却把该比对的**触发条件**写成自声明 `decl["legacy"]` ⇒ 冻结集内文件「body 篡改 + 重标 `phase=contract pair=<已上产 expand>`」**实测 rc=0 绕过**（上轮 P0-1 修复未闭合）；② 规范 §1.1 声明的对象是**裸 SQL 语句**（`DROP TABLE` / `RENAME TO` / `ALTER COLUMN … TYPE`），实现只匹配**全大写拼写** ⇒ `op.execute("drop table demo_t")` **实测 rc=0**。
- **判据**：先问「**规范原文说的是什么**」——若规范已批准且实现与之不符，则改动是**让实现回到规范**（bug 修复），不是改设计；只有当**规范本身**要变，才需重新拍板。
- **修法**：P0-A＝冻结集校验触发条件改按 `name in LEGACY_FROZEN`（与声明解耦）；P0-B＝判定表/排除表匹配统一加 `re.IGNORECASE` + 对齐 docstring 声明的宽口径。

**决策 2 —— 「**约定**（convention）」不构成安全降级理由；**仓内现状快照 ≠ 守卫**
- 事实：P0-B 有一处看似成立的反驳——「仓内 141 个迁移**当前 100% 大写**（实测），故小写路径低风险」。该反驳**不成立**。
- **判据**：与上轮「**故意性**不能区分 `legacy` 与 `allow_contract`」**同族**——一个依赖人的自觉、无机械执行者的状态，正是本仓反复认定的「**纸面约束**」。「当前没人这么写」是快照，不是闸门。
- **推论**：判定表是**语汇黑名单（deny-list）**——按既有纪律「deny-list 必漂」，其漂移实例（大小写、`TRUNCATE`/`DELETE` 等数据类 op 未列）**必须显式写进规范 §6 已知边界**，不得以「没人这么写」默认无害（否则重演「未声明即静默」）。

**副产判据（可复用）—— 「集合不闭合」是静默漏检的共同特征，可升格为机械验证点**
本轮两处 P0 的共同形态都是「**门报绿而实有缺口**」：一处是**该管的集合少了元素**（检测器失灵），一处是**不该放行的元素进了放行集**（自声明旁路）。⇒ 建议的机械判据：「**全仓重扫的命中文件集，必须精确等于 `LEGACY_FROZEN` ∪ {当前合法 contract}**」——多一个/少一个都红。已挂账（`flow/待办.md` 🔖 行）。

## 2026-10-08 · 批 111 步 4 代码双盲审必修两条（豁免通道须**枚举等价路径**；退出码**语义唯一**是补集成立前提）

**背景**：批 111 步 4 代码双盲审（同判 `flow/稳定性检查/盲审迁移两步走立法-步4代码-同判综合.md`）：A（无 P0）/B（1 P0），主会话仲裁 **2 P0**。

**决策 1 —— 「豁免 / 逃生通道」加固时，必须**枚举所有能达成同一效果的路径**，不得只硬化其中一条**
- 事实：批 111 花四轮把**旗标** `allow_contract` 硬化为「带 `contract_reason` + 落 `var/` 留痕」的声明式旁路；但**同一批**留下的**文件标记** `legacy` 达成**完全相同**的效果（破坏性 op 过门）且**零验证、零 reason 校验、零留痕**——比旗标更易达、无人看守。
- **判据（为何是 P0 而非 P1）**：① **声明面被执行面消费**——设计的部署门职责原文＝「拒未声明者」，对 `legacy` 却「消费声明而不裁断」（信任）⇒ 该路径未闭合；② **单一真源被切两半**——「哪些 legacy 合法」的清单住在**测试文件**、执行侧模块无副本 ⇒ 执行面上该真源不存在；③ **模块自相矛盾**——同模块内 `evaluate`（拒）与 `check_release`（放行）对同一事实持两立场。
- **修法**：清单真源搬进模块（`LEGACY_FROZEN`），门对 `legacy` **验证而非信任**——名在冻结集 ⇒ 不阻断**且** `upgrade` 段须与已部署版**逐字一致**；名不在 ⇒ **拒**。连带 `legacy`/`phase` 互斥校验。
- **驳回「靠 PR 期 pytest 拦」**：部署链零 pytest/无 CI/无钩子（W6#1 自认）⇒ 该拦阻在 prod 上不存在，属未落地假设。

**决策 2 —— 封闭式补集消费退出码时，**生产侧必须保证「码 → 语义」唯一**
- 事实：`failed_when` 用封闭式补集消费 `rc`（`rc=1 ⇔ 真命中`）；但 `check_release` 仅包了 `managed_set` 的 `OSError`，循环内非 `OSError` 异常（可达：`UnicodeDecodeError`，属 `ValueError`）⇒ **模块崩溃退出码 = 1**，与「真命中」同码 ⇒ `allow_contract=true` 时被当命中豁免 + 旁路告警**谎报**「门已检测」。
- **判据**：与三审 P0-α **同构**（判据原文「门不可用恒红」）——补集**依赖生产者不串码**；生产者不保证，补集即不成立。
- **修法**：`check_release` 整段包 `except Exception` ⇒ 任何内部错**恒返 2**；`evaluate` 不再把读错误吞成「拒」。`rc=1` 语义唯一化＝「有命中且拒」。

**副产教训（进 skill）**：评审「异常 ⇒ rc=?」类结论**必须给可复现构造**——本轮两名盲审员均把触发路径猜成「权限错/TOCTOU」（实测被 `try/except` 兜住），真路径是**解码失败**；**触发路径猜偏 ⇒ 修复会打偏**。

## 2026-10-07 · 批 110 立项裁定：删冻结＝**分族**动作；§八.5 门控须加「可及性前提」

**背景**：母设计 §八.5 删除门控「连续 N 轮同缺口零误报」在**执行面四者皆缺**（判据本体／观测／观察面／前提）。步 2 双盲审两轮（首轮 N1–N8 ＋ 复审 V1–V7）**均过门零 P0**；权威＝`flow/稳定性检查/盲审批110方案-复审同判综合.md`。

**决策 1 —— 冻结删除是**分族**动作，不是全局动作**
`binance_perp_*` 族无标的级生命周期真值（`symbol_inception` 仅 OKX 覆写；`onboardDate` 零使用；裁定 F 显式未知）⇒ 该族对账被 §5.4 `uncertain` 抑制 ⇒ **冻结是其唯一兜底** ⇒ 对该族删除**永不成立**。⇒ 按族分治：OKX 可删／Binance 保留。
**驳回**「`unreachable` 盲区登记即可解 Binance」：盲区登记要求 inception **已知**才能算区间起点；「下界＝`source_earliest`」对晚上币标的产生**逐标的假缺口**；「本地首日」当下界违 §八.2。它是「**per-symbol 的假缺口面**」，非共有盲区。

**决策 2 —— §八.5 门控判据须扩写「该族 inception 真值可读」这条可及性前提**
以 `_RECONCILE_UNKNOWN_INCEPTION` 显式声明 ＋ **互证闸**落地（比对键＝「SM 有 symbol 且 `list_date` 空」，**只在快照新鲜时评估**，防「瞬时探不到」误判为结构性）。

**决策 3 —— 「连续 N 轮」的轮次身份＝**严格单调任务序号**（Valkey `INCR`），维护语义含**断档复位**
判定式 `hit_rounds>=N 且 last_hit_round==本轮` **不充分**（跳轮不清零 ⇒ 中断被当连续）。⇒ 维护时 `hit_rounds = (hit_rounds+1) if last_hit_round == round_id-1 else 1`。`round_id` 用 Valkey `INCR`（每轮入口无条件推进）；**不用**裸墙钟（同秒重跑／NTP 回拨）或 DB `MAX()+1`（全族跳过时不推进）。

## 2026-10-07 · 批 108 步 4 盲审必修两条（SM 行元组索引 ＝ 同源；`policy_discard` 判据用已知下界）

**背景**：批 108 步 4 代码双盲审（同判 `flow/稳定性检查/盲审批108代码-同判综合.md`）。

**决策 1 —— SM 行元组的索引取值必须与 df 元组**同源**（同一次改动内成对审查）**
- 事实：`_sync_cb_basic` 的 SM 行取 `r[12], r[13]`，而该 handler 的 df 元组是 15 元
  （`r[12]=rate_clause`、`r[13]=list_date`、`r[14]=delist_date`）⇒ `list_date` 落 NULL、`delist_date` 落上市日
  （dev 实测 1160/1172 行「上市即退市」）。
- **判据（为何是本批必修而非既存债延后）**：**契约闭合**——步 2 的裁定 G① 已把 `SM.list_date` 变成被消费的
  真源（`_get_list_date`）⇒ 使该真源正确的责任随消费者一起落到本批。文件亦在本批 `限定范围`（`data_sync/engine.py`）。
- 一般化规则：**`_sm_upsert*` 的元组索引是"位置契约"**——`session_id` 追加在末尾让**长度**可校验，
  但**位置**（哪一列取 df 的哪个下标）**不可由长度校验保证**。凡改 df 元组列序 ⇒ 必须逐一核对同 handler 的
  `_sm_upsert`/`_sm_upsert_state` 全部下标。**「13 列且 `r[12]` 非空」不是语义正确的充分条件**（`r[12]` 恰为非空的 `rate_clause`）。

**决策 2 —— `policy_discard` 判据 ＝「已知下界」`max(inception, source_earliest)`，非仅 `inception`**
- 事实：原判据 `retention > inception` 令 **inception 未知的源**（裁定 F：Binance 落 NULL）在 `retention` 截窗时
  **永不登记**（静默封顶）；存活实例 `binance_perp_hourly` 静默弃约 7 年。
- 判据：§5.3「**封顶不得静默**」的落点是**段**，不是**上币日**——只要已知下界存在且 `retention` 晚于它，
  该段就是「我们主动不保留」⇒ 必须登记。**未知 ≠ 无封顶**。

---

## 2026-10-07 · 窗口边界模型 §十 裁决点 A–H **裁定**（威廉姆「完全接受建议」）

**背景**：设计稿 v3 步 2 已过门；§十 列 A–H 待裁。裁定用展开单＝`flow/方案/同步窗口-待裁单A-H.md`（选项 × 具体形态 × 牵连面 × 架构判据 × 三方结论）。

**裁定（8 条；判据＝架构合理性 > 实施成本）**：

1. **A 扩 `security_master`（有条件）**——inception 取真上币日。**连带须明写**：A 使 crypto 从「无档 fail-closed 恒拒」变为走 `category=='perp'` 分支（`perms.py:99-118`）＝**激活 perp 权限路径**（实测三账户 `allowed_categories` 无 `perp` ⇒ 仍拒、无意外放行，但语义已变，属有意激活）。
2. **B `retention`：任务列为准 ＋ kind 默认作默认值来源**（读入时合并）；封顶须 `policy_discard` 显式。
3. **C 对账频率：不设全局值，按族/kind**（per-date 每轮 / per-symbol 低峰或周级 / 快照不对账）。
4. **D 先 per-date**（判据 `trade_cal` 已存在、不依赖 A；替代在跑的 `_TIER1_OVERLAP_DAYS`）。
5. **E 立孤儿参数键闸门**（列级 ＋ 带理由豁免 ＋ CI 强制）。
6. **F 真上币日不可得 ⇒ 显式「未知」**（禁近似冒充）；未知态铰入 §7.2 输出（`uncertain`、抑制缺口主张）。
7. **G 择①：SM 为真上游、`_get_list_date` 改读 SM**。**理由**：三列表同步（`engine.py:643/677/716`）**已双写**原表＋SM（同一 `df` fan-out，非双源）；crypto 无原表可投影 ⇒ ②必出例外，使同表对 astock 是「投影」、对 crypto 是「原始」。B2 倾向②的前提「SM 不新增写路径」实测不成立。
8. **H 拆 `start_floor` 一列三义**：retention 留 `sync_config`（引擎真读）／源可达边界移入 `available_range`／`inception` 归 SM。逐行判**仅 7 行**（30 行里）。禁「语义标列」软约定。

**立项**：A–H 定为实施前置；立项 **批 108**（`flow/任务/批108-同步窗口边界模型.md`）。**文献**：`flow/方案/同步窗口-待裁单A-H.md`。

---

## 2026-10-07 · 源可达下界「须实测、禁臆造」＋ retention 封顶须显式（窗口边界模型步 2 复审②）

**背景**：`flow/方案/同步窗口与参数分层-设计.md` 步 2 复审（v2 → v3）。两名复审人**无 P0**，但在 Binance 源界取值上**正面对撞**。

**裁定**：

1. **源可达下界是「测量结果」，不是可推断/可臆造的声明。** 禁以「标的上市日」「某粒度包的全局 floor」「配置里已写的值」代替实测。**先例即本仓既有纪律**：迁移 `0136` **故意留 NULL**（OKX 最早日未实证）、`0137` 序言明写「`start_floor` 只填**已实证**下界、其余 NULL，**不臆造**」——本裁定只是把它从「源下限」**推广到 `inception`**（故「真上币日不可得 ⇒ 显式『未知』」，设计稿待裁 F）。
   **理由（本轮裁定的直接动因）**：`binance_adapter._pull` docstring、迁移 `0132` 注释、DB `start_floor` **三处一致**地称 Binance 日包可达 `2019-09-08` —— 主会话对 `data.binance.vision` 实测：**日包 `2019-12-30`→404 / `2019-12-31`→200**，三处**全错**，因为三者**同源**（0132 写入 DB、docstring 与 0132 注释互相沿袭同一假前提）。
2. **⭐ 方法论（长期有效）：多份证据一致 ≠ 互证，须先问是否同源。** ①轮的教训是「两代理一致也不等于对」；②轮补「同源三证也不等于三证交叉」。任何「三证吻合 / 多处一致」的论证，先做**独立性检查**（是否同一写点、互相引用、同一假前提派生），再做**一手实测**。
3. **`retention` 封顶期望集时，被放弃的区间 `[inception, retention)` 必须显式登记为 `policy_discard`（含理由）**，禁由「下界取 max」隐式吞掉。**理由**：静默封顶＝「误种一个偏晚的 retention」即无声丢史，且与「源头就没有」同形——**新静默阀**。同批：源不可达段 `[inception, source_earliest)` 标 `unreachable`（须实测）。
4. **`sync_gap` 表是派生视图、非真源**：可清空重建，**禁止**任何窗口/期望集计算读它（否则派生值回流成真源）。
5. **`start_floor` 一列三义须拆**（见上条 2026-10-07 决策第 4 条）：retention 留 `sync_config`（引擎真读）／源可达边界移入 `available_range`／`inception` 归 SM。**不设「语义标列」这类软约定**（软约定＝没约定，不可 CI 化）。
6. **过门**：步 2 **通过**（双同无 P0；P1 全部有处置）。**A–H 已裁定**（见本文件顶部同日「A–H 裁定」条）⇒ 立项批 108，进步 3。

**同车记录**：`_pull` docstring（:246-249）/ 迁移 `0132:16` / `_FLOOR_DATE` 分支（:255-263）基于假前提——不修则后人按错前提设计，且长区间每次多烧约百次必 404 的日包请求（「省流阀」实为「费流阀」）。**文献**：`flow/稳定性检查/盲审窗口边界模型-复审-同判②.md`。

---

## 2026-10-07 · `inception` 不得取自「源可达性探测」（窗口边界模型步 2 双盲审① P0）

**背景**：窗口边界模型设计稿（`flow/方案/同步窗口与参数分层-设计.md`）步 2 双盲审，两代理**独立收敛**同一 P0：§八.2 拟「把 Binance 月包下限解析成 `list_date`」。

**裁定**：

1. **`inception`（标的产生时间）只能是「事实上的上市/上币日」，不得由任何「源可达性探测」冒充。** OKX 取 `listTime`（已在响应内、现被丢弃）；Binance 取真上币日（`exchangeInfo.onboardDate`）；**fapi 被墙时须显式标「inception 未知」，不得用 `first_available_month` 兜底**。
   **理由**：可达性探的是「源从哪个月起有包」，不是「标的何时存在」。拿它当期望集下界 ⇒ **源比我们更早知道的行情被判为「不存在」**——错误性质从「拉不到」（可见）恶化为「**不认为缺失**」（静默）。**实证（两手）**：OKX `listTime=2019-11-12` 而 K 线可达下界 `2020-01-01`（差 ≈2 月，0137 prod 实测）；Binance 上线 `2019-09-08` 而批量 S3 日包可达下界 `2019-12-31`（差 ≈4 月，主会话实测）。⚠️ ①轮曾据 `binance_adapter.py:246-249` docstring「日包更早、否则静默丢掉 2019-09~12」**坐实**此 P0 —— 该 docstring **已被实测证伪**（日包 pre-`2019-12-31` 全 404）；**结论不变、证据换真**，详见 `盲审窗口边界模型-复审-同判②.md` §三。
2. **窗口是四边界交集**：`[ max(inception, retention, source_earliest) , source_latest − publish_lag ]`。**源下界与源上界同为源属性**，须由 `available_range(kind) → (earliest, latest)` 一并返回；未实现的 pull-capable adapter 即 CI 红（fail-loud），**禁适配器内部私自收窄起点**（否则「模型窗口 ≠ 真实窗口」，对账层不可信）。
3. **§十 待裁点 A 结论修订**：由「同意」改为「**有条件同意**」——条件 = ① `inception` 取自真上币日（本条 1）② `upsert_rows` 补 `session_id` 通道。B/C/D 不变；**E 追加「CI 强制」**。
4. **`start_floor` 正名须逐行判**：同列已混装**三义**（②轮 DB 实测升级：`binance_perp_daily=2019-09-08`＝**上币日/inception**、`okx_perp_daily=2020-01-01`＝**源下限**（0137 明写「实测 K 线边界」）、`binance_perp_hourly=2026-09-29` 与 `15min`/`1min=2026-10-05`＝**retention**）⇒ 不得按 provider 一刀切迁移，**且无法用规则自动判语义**，只能逐行判（设计稿待裁 H）。

**同车更正设计稿三处事实错误**：窗口起点站点 **4 处非 2 处**（`engine.py:998/1202/1497/1555`）；Binance `2019-09-08` 是**上线日**（**不是**任何「包」的下界：月包 floor=`2020-01`、**批量日包可达下界=`2019-12-31`**，②轮实测）；「`crypto_perp_daily` 孤儿调度」已过时（改名 `binance_perp_daily`，旧名仅存 69 条 2026-10-06 的 error 日志）。
**文献**：`flow/稳定性检查/盲审窗口边界模型-同判综合.md`（含 A/B 两份原报告索引与必修清单）。

---

## 2026-10-04 · 看板类只读接口：失败必须可见 + 只读重查询放宽超时（批 91）

**裁定**：
1. **看板/度量类只读端点禁止静默吞错**：`except` 不得返回与「真无数据」**同形**的空体——须
   `logger.exception` + 响应体带 `error` 字段（前端据它亮错误条）。**一个「用来发现数据缺口」的
   页面，自己的失败却是静默的——这是最大反讽**（与 OBS-1 同族：可观测性亏损）。
2. **只读重查询单独放宽 `statement_timeout`**：经 `db.py::get_conn_for_heavy_read()`
   （事务级 `set_config(..., is_local=true)`，退出自动复位）。web 进程默认 10s 对 `bar_1d`
   全表聚合过紧。**放宽只作用于单一事务，不改全局策略**；超时逻辑落层 1（路由加 SQL 会撞
   `test_no_sql_in_new_routes` 基线闸）。
3. **降级优于 500**：整页不可用（500）比「部分列不可用 + 显著标记」更差 ⇒ 聚合失败降级 +
   `count_error` 标记，**不把「查不到」伪装成「本地 0 条」**。
4. **停用即停用**：`sync_config.enabled=false` 的行，界面不得再显示其残留 `last_status`
   （显示「已停用」）——否则停采项（永不运行 ⇒ 状态永不刷新）会**永久**挂一个红「失败」。

**理由**：prod 无任意 SQL 通道 ⇒「数据面验证」**只能**经 DataOps 页；这两个接口不可用等于
**验证能力归零**而无人知。批 91 恢复该能力。

---

## 2026-10-04 · 工作流程分两级：项目主流程 vs 单任务流程

**裁定**：`flow/规范/工作流程.md` 立**两级**——
- **项目级**（§一 项目主循环）：立项（`charter.md`）→ 规划（`plan.md`，计划即契约）→ 迭代交付 → 沉淀 → 验收/演进。
- **任务级**（§二 单任务流程）：取项 →（设计变更则）设计先行 → 任务文件 → 挂待办 → 执行 → 收口；**强制门禁主干 = `八步法.md`**（独立文件）。

**理由**：`八步法` 只管**单个任务/单批**的交付门禁，**不是**项目主流程；`charter` / `plan` 属项目级，一个任务不走这两步。此前版本把两级揉在一起（自称「五段式主循环（每个任务走一遍）」）＝层级错乱。
**判据**：涉及 `charter` / `plan` / 里程碑 → 项目级；涉及单个任务的文件流转与交付 → 任务级。

## 2026-10-04 · 内容分层 + 两制式（规范层归一）

**裁定一：待办只做索引**。`flow/待办.md` 从 915 行压到 ~120 行——一行＝一个未闭合项（概要 + 状态 + 指针），**禁堆实施细节 / 上产记录 / 裁定推理**。
**理由**：膨胀根因不是内容多，是四类内容混住——读一条待办要先过滤掉 90% 的史，而那些内容在任务文件与进展条里多为副本。

**裁定二：两制式**（本轮新增，为分层提供纪律）——

| 制式 | 适用 | 文件 |
|---|---|---|
| **追加制**（append，新的放最上面） | 流水 / 事件 | `flow/进展/<周>.md` · `flow/踩坑记录.md` · `flow/decisions.md` |
| **重写制**（改就重写整篇，保持自洽） | 状态 / 规则 | `docs/architecture/` · `docs/design/` · `flow/方案/` · `flow/规范/` · `CLAUDE.md` |

重写制的操作：**重读全文，把变化揉进正文各处，删掉被取代的旧表述**；允许保留**一行时态声明**；变更史去 git log。
**判据**：新会话第一次读这份文档，能否**直接**得到「现在的真相」——要翻时间线才能拼出来 = 不合格。
**理由**：状态文档用追加 = 把它变成反反复复的时间线，读者要拼才能得到真相——这正是「信息污染」的形态。

**裁定三：分层归属表的真源上移至规范**。归属表（内容 → 归宿）唯一真源 = `flow/规范/工作流程.md` §三；`flow/待办.md` 表头只**引用**不复制（复制 = 两处真源）。任务生命周期（四环节）与该表同在 `工作流程.md`。

**配套判据**：以上产「代码是否已 merge 到 main」为准，不追「是否声明已上产」——merge 后任一 release 都携带它（批 72/73/75 由此从「未上产」修正为已上产）。

## 2026-09-28 · 总方案 v3/v4 三条裁定

| 裁定 | 内容 |
|---|---|
| break-glass 不做 | 个人系统极端情况走传统路径（手机券商 app）可处理；服务器挂了 shell 命令也死——传统通道最保险 |
| **两域立法** | 实盘行情账号（XTP/EMT/币安/OKX）= hub 架构；tushare 类数据提供商 = 数据同步工作——业务不同不强捏合（方案 §〇） |
| **多源刚性** | 设计必须满足多源可能，不要先做死再改——`sync_config.provider` 配置页已有但仅是摆设（只有 K 线真路由），激活 = 批 83b |

## 2026-09-28 · 深水区三项勘察结论（批 71 期间完成）

| 项 | 判定 | 依据锚点 |
|---|---|---|
| ② 实盘任务绑定切行 | **标废**（D26 已消化） | `live_task.account_id` NOT NULL+FK RESTRICT（0100/0104）；行内改 provider→hub exit 9 重建、account_id 不变；全仓无 UPDATE account_id |
| ③ `strategy_account` 语义 | **标废**（0101 已 DROP） | 策略 = 配方不绑账号，多账号经 live_task 三元组 |
| ① 熔断进程键多账号 | **仍存——批 73 动机证实** | `_LIMITERS` 键=(provider,api_name)、`_BREAKERS`=provider（`rate_limit.py:154-155`），账号未进键；prefork + `--max-tasks-per-child=100` 记忆清零；批 74（→83b）第二源引入时转 live。**新增裁决点**：键升维 `(provider,account_id)` |

## 2026-09-28 · 候选池讨论结论

| # | 候选 | 裁定 |
|---|---|---|
| F1 | 冻结事件事实表（起止/原因/水位/解冻方式） | ✅ 做（不等实战——建表成本低，记录有价值）→ 批 76 `freeze_event` |
| F2 | 分级解冻（gap 型自动 / ts_gap·untrusted 人工） | ✅ 做（自愈 = 设计要求）→ 批 76 |
| F3 | 告警催办升级（未处理通知循环） | ❌ 不做（通知不可达时催办也没用；收到了自然不用催） |

## 2026-09-30 · 插件分层判据（差异下沉 / 共性上提）

**裁定**：不同的部分（数据源特定的「怎么拉」，如 Tushare `pro.daily` vs Wind `wsd`）下沉到插件；通用的部分（游标推进/调度/幂等 upsert/质量校验/血缘，对所有源一致）保留在上层引擎。这才是最经济、最合理的。

**与抽象判据互补**：抽象判据回答「何时合并」（功能类似/有继承才抽象成一类实体）；本判据回答「何时拆分/分层」（差异下沉、共性上提）。两者是同一设计哲学的两面。

**落地**：同步任务拆两粒度——拉取（数据源特定，已下沉到 `adapter.fetch` 契约，批 58）+ 编排（通用，留在 `data_sync` 引擎）。多源支持的正确形态=新增 adapter 实现 fetch + 注册，引擎零改动。

## 2026-09-29 · 数据源/交易账号拆分（抽象判据裁定）

**裁定**：数据源账号（Tushare 拉取）与交易账号（XTP/EMT 下单+行情）是两类**完全不同、无继承关系**的实体，不应揉合在 `external_interface` 一张表。拆成 `data_source` + `trading_account` 两张表 + 前端两页签。

**抽象判据（通用原则，用户裁定）**：功能类似或存在继承关系才抽象成一类实体；完全不同的实体强行统一=增加复杂度，拆分更简洁。`external_interface` 的揉合（能力 token 强行统一两域）是「错误统一」的实例——能力 token 的初衷是「一个接口多能力」（XTP 同时行情+交易），被误当成「两类账号的统一层」。

## 2026-09-29 · 技术债第三梯队方向（用户拍板）

- **P0-2+P1-2 消费链数据洞**：**做**（实盘消费链顺序修复 + guard 语义；高风险，改前 staging `test-live-pipeline.sh`）
- **P2-3 因子聚合语义**：**统一**（`strategy.py` 除 `w_sum` 对齐 `weighted_avg`，分数恒 `[-1,1]`；需同步调存量阈值 + 回测基准）
- **P2-9 选股权重**：**迁 DB**（先 seed 现值 2.0/1.0/1.5/1.5 保证行为不变，再切读取源）
- **P2-4 权重键 fail-closed**：做（低风险，随批）

## 2026-09-29 · 观察 gate 不阻塞后续批（持续观察）

**裁定**：`[quality]` 数据质量观察（批 71 起）不再作为阻塞后续批（72/73）的硬 gate，改为**持续观察**——后续批正常顺序上产，观察作为部署后的持续监控（发现异常→告警+处置，不预先卡住）。

**理由**：观察 gate 的阻塞语义让质量校验从「监控手段」异化成「发布闸门」，卡住本可推进的批次；质量校验的价值在持续发现异常，不在预先放行。

## 2026-09-29 · 删除 shadow 对账（同源自检无意义，批 79）

**裁定**：删除 shadow 行情主备对账功能，保留 SM 对账（`sm_reconcile`）。

**理由**：shadow 对账主源 bar_1D 与备源 Tushare 现拉底层同源 `pro.daily`，是「拿自己和加工过的自己比」——只能检出入库链加工 bug，抓不了源侧系统性错（一致地错），还会假绿（备源拉取失败→backup 空→real_diff=0 被白名单吸收）。真正的交叉验证需第二独立数据商（Wind/聚宽/米筐），留作多源接入后的理想态（A03 §15.2）。

**保留**：SM 对账（security_master vs static_symbols 两张表互对，不涉行情同源）+ validate_bar_quality（批 71 单源内部一致性：跳空/断点，不依赖备源）+ lineage 血缘。

## 2026-09-26 · A04 §九实施语义 Web 域诚实化（批 61 M6）

1. **两条立法降级实施**（立法意图保留，A04 §九注记为实施真相）：①「提名时刻连通预检」Web 域不可达（`Broker.test_connection`=纯凭证检查零网络；Web 进程无 TD 会话）→落地 **L1=凭证完整性+行校验**，L2 真连+资金挂账 worker/hub 会话探测批；②「在途清零」两源（order_log 无终态回写——成交/撤单不落 status）→DB 近似+确认者人工核实勾选，order_log 终态回写根治挂账。
2. **确认面=Web 五闸等价**（29b 卡绑飞书 ws 不可直连）：时效=DB 时钟 SQL 谓词/身份=JWT/权限=require_perm（execute=trade 键 analyst 不可达）/dedup=状态机+FOR UPDATE/exec=单事务。
3. **TradeBus 四方法=worker 链继承**：place/cancel/query 已在 worker 进程落地（时序纪律/幂等全现成）；Web 手动下单永不做（XTPAdapter 无 gateway 返 mock- 前缀=实盘事故面）；幂等键不迁移（`t{tid}:e{boot}:c{seq}` 前缀结构性唯一强于复合键）。

## 2026-09-26 · TD provider 插件注册表立法（批 65 D26-A/批 63 P4 实证）

1. **TD builder 必须注册于 `strategy_runner/td_registry.py` 模块内**（import 即注册；独立文件注册=空表静默）——单文件自足约定。
2. **键域守门锚 `PROVIDER_MARKET` 非 `Broker._REGISTRY`**（后者无 emt_emq=前向地雷；两角色非 1:1——crypto 有 Broker 无 TD builder）。
3. **EmtAdapter vnpy 形状合成为强制契约**：worker 四消费链（reconcile/halt_edge_cancel/write_trade_log/snapshot_cycle）假定 vnpy OrderData/TradeData/AccountData+事件引擎——任何新 TD adapter 必须在 SPI 回调内合成 vnpy 形状经 ee 推流，**查询分帧必须帧内物化**（SDK 指针仅帧内有效）。

## 2026-09-25 · 全市场统一账号级 hub 拓扑（推翻 A05「A股 MD 单 hub」部分裁定）

1. **终裁**：全市场唯一拓扑=**账号级实例化**——一行 trading 域接口配置=一个账号=一个 hub+一套 TD 会话；hub 绑死账号行（MD/TD 同凭证，裁定 A：不做 md_row 跨通道解耦）。XTP 双账号=双行=双 hub（空间换简洁）。
2. **推翻 A05 五理由**：①分支税（A 股/加密 10+ 处分支，D6 键分叉/flush_stale 等历史 bug 本质是分支税）；②故障域反转（单 hub=全平台单点故障；per-account=故障域恰为账号域——A05「N-1 多余故障点」视角是反的）；③一致性反对弱化（两份聚合 bar 微差在 A03 双时态语义下非新罪：流生成版本本就允许微差，权威=盘后 PG 回补）；④M5 退役红利（互备体系存在的全部理由=多实例抢一键空间，统一后无对象，switch.py/active_instance/intent/guarded Lua/AB 双实例整体蒸发；gen 保留，lease 简化）；⑤**底座同质化**（最根本：「聚合 hub」特殊形态组件消失——hub 从共享基础设施退化为账号附属进程；若保留共享单例，hub 底座须双模式注册，解耦复杂度落在底座上传染全系统；统一后单一形态=注册即行 CRUD、代码路径唯一、故障模式单一）。
3. **连带退役**：HUB_INTERFACE_ROW 概念（hub 选行语义消失）；position/拖拽的通道切换语义（reorder 端点退役；tushare 多数据源行优先序与 routing 软排序保留）；SA4 期望态统一按行（A 股特例消失）。
4. **不变式修订**：「同源数据不复制」全市场统一为 **best-effort**（对账锚=PG 盘后回补；消费方不得假设跨账号同 bar）。
5. **哲学（用户立法）**：逻辑越简洁代码质量越高，真实故障点越少；资源不足加配置即可。
6. **载体**：`docs/design/D26-市场接入架构.md` v3（§零推翻记录）；**实施批方案细化须重新双盲审**。

## 2026-09-24 · 术语正名：venue 概念废除（账号→account、交易所→exchange）

1. **venue 一词彻底废除，按语义拆两词**：D1-D6 的 venue（=交易账号/external_interface 行）→ `account`（对齐 vnpy `AccountData` / QuantConnect `account` / FIX Tag 1 `Account`——行业里 venue 本义=交易场所/交易所，拿来指账号是语义错位）；`MARKET_OP_DECOMP` 第三元（=BINANCE/OKX 交易所）→ `exchange`（归一到已有 `EXCHANGES` 概念，本就该叫 exchange）。
2. **迁移策略**：新增 0104 rename（6 表 `account_id` 列 + `account_permission` 表 + 18 约束 rename + 对账数据 UPDATE）；历史迁移 0097-0103 不改（alembic checksum 不可变，squash 需回滚破坏性迁移有损）。交易所语义 venue 仅存在于注释/docstring（代码标识符早已用 exchange），归位零 DDL。

## 2026-09-23 · 多账号源架构重设计（30 号废弃 → 31 号框架 + D1-D6 详细设计）

1. **重设计方法**（用户裁定）：30 号 v1-v15 共 15 轮 4 盲审未收敛 → 全部重来、换 session。方法=**先写简要但明确的整体框架方案（定义架构+契约）→ 再分头写几个小详细设计方案**。30 号标「废弃」仅参考，不在其上改。
2. **评审角色换「量化交易高手」替「金融专家」**（方法论裁定）：金融专家会求全金融合规边角（与个人平台定位冲突），量化高手只审「真正影响交易」；框架双盲审=软件架构专家+量化交易高手两角色。
3. **Account = 交易账号**（external_interface 行），非「券商」；同源分市场：A股=MD 单 hub（L1 全市场同源，doc14 M5 保留）+ TD per-account；加密=MD+TD per-account。同源校验=跨进程运行时事实（流带 account_id，worker 比对），非「同一行派生两值」空转断言。
4. **权限三维正交 + 数据字段化四首决**：board **新增枚举列**（main/star/chinext/bse，从 asset_static_info.market 中文归一化回填，否决复用 market 列——中文文本当键=同类漂移）；account 权限存 **新表 account_permission**（否决 external_interface 加列/扩 live_trading_config）；is_st 官方名单 fail-closed；强赎/退市整理期是价格事件需字段（非边角排除）。
5. **身份与数据隔离三首决**：稳定语义键=**资金账号**（external_interface.account_key UNIQUE，股东账号沪/深各一不适合单列键）；**leverage→account 级**（账号级，账户级敞口实参，券商/交易所约束）；资金基线写侧 per-account（initial_capital 不再取策略级默认）。



1. **历史分钟数据「不自攒、买正规」**（用户裁定）：自攒 A 股分钟数据质量存疑（腾讯攒竞价条错位 / 320 根滚动窗口漏一天断 ~4h；XTP bar_hub 自攒与实时分发耦合），维护成本高。退役自攒链路（断档接受），将来买 Tushare `stk_mins`（2000 积分/年）正式数据接入。设计真源 21 号本就是「腾讯攒过渡 + Tushare 终极」，本次提前结束过渡期。
2. **边界 = 删「攒」留「读」**：删腾讯攒 + XTP bar_hub 落库 + minute_symbols 管理面；保留实时行情分发（`MinuteAggregator` + XADD 流——实盘下单输入，行业标准打法）+ stk_mins 接入位（`engine.py` 分钟编排 + `TushareAdapter`，将来买积分即用）+ 日线。`bar_1min`/`bar_5min` 表结构保留（将来 stk_mins 填），只清空腾讯存量。
3. **分钟数据源切换语义简化**：`minute_data_source` 互斥开关随腾讯攒删除——将来 stk_mins 接入不需「互斥切换」，直接启用 `engine.py` 的 stk_mins 路径（21 号 §3.4 切换逻辑随之简化）。
4. **断档影响接受**：因子试算 1min/5min 档硬失败（返回「无数据」）、实盘暖机 history 空（流回放 240 根兜底、double_low 静态因子不受影响）、Web 覆盖状态/完整性看板 1min/5min 显示空——均无交易/回测硬断。

## 2026-09-23 · 批 63 部署三裁定 + 批 64 Web 切换裁定

1. **EMQ 编译链用 gcc-toolset-13（不 patch vendor 头）**：EMQ 头 `quote_api.h` 用 `EMQ_EXCHANGE_TYPE::EMQ_EXCHANGE_UNKNOWN`（C++23 P1099 enum 作用域限定），服务器 GCC 10.2 编译报错。用户裁定装 gcc-toolset-13（`source /opt/rh/gcc-toolset-13/enable`），不改东财 SDK 头（保留上游原样，升级 SDK 时不丢 patch）。
2. **切换 provider 用「自动重启」而非「热切换」**（批 64）：hub 对账 `config_version` 检测 provider 变化 → `os._exit(9)` 自重启读新行。不进程内 close 旧 gateway + 建新——违背项目铁律「原生库拆除规避」（hub 退出走 os._exit 带码自灭）。代价 ~30s 停机窗（盘外零损失）。
3. **外部接口切换纯 Web 操作**（批 64）：集成中心拖拽改 position + bump config_version 即触发，不需 drop-in/root。`HUB_INTERFACE_ROW` 是 M5 双实例（A/B 账号）机制，非 provider 切换场景。

## 2026-09-22 · 批 60 M5 流协议五裁定（方案集 v16 定稿）

- **M5 切换协议 = Valkey 四键 + guarded Lua + 无待命态**（推翻 28/29 蓝图的 stream_registry/影子预热/fencing token）：`hub:gen/lease/switch:intent{snapshot,target}/active_instance` 四键承载全部状态；B=A 让位后按需启动的正常 hub（非提前待命——main.py 单遍初始化决定「接管≈全量重启」，待命省不了连接时间）；「计划切换零丢包」降级为「切换仅限非交易时段 + 盘后回补兜底 + `switch.py diff` 口径比对」并已版本化注记进 28 §8.2/29 §八。
- **guarded Lua 三态原子是切换核心**：首接（gen==snapshot→INCR+抢 lease+SET active_instance）/重启（gen==snapshot+1→只抢 lease）/污染（→拒，校验在 INCR 前零污染），正切 B/反切 A 通用；`active_instance==target` 是「目标已接管」的等强度代理（三等价：active_instance==target ⟺ guarded 成功 ⟺ 持 lease），由此砍掉 HUB_UUID 预定注入（无特权通道回写 drop-in）。
- **boot 单判定 + intent 记 target**：切换方向建模在 intent（正切 target=quant2/反切 target=quant），boot 闸据此放行目标/拦截被切走者（exit 6 Prevent）；active_instance 只兜「切换完成后服务器重启」仲裁，normal 冷启也 SET（首启即生效）。
- **退出码矩阵 Prevent=0 6 78，3=真让位回归重启码**（staging 实证）：SIGTERM 不 DEL lease，正常重启有 30s lease 滞后窗，靠 exit(3)→on-failure 30s 重拉自愈；切换窗复活拦截由 boot 闸 exit(6) 独立承接——一码多路径，Prevent 决策逐路径判定。
- **评审方法论**（用户原则沉淀）：方案打磨期「换强模型对抗式单轮」优于同模型多轮（两轮 opus 深审独立命中同一 P0）；**自己先往死里推演主场景（正切/反切/重启/崩溃）再交评审**——v11 boot 双闸正反切矛盾即自推演发现，评审抓「设计对不对」、自推演抓「实现会不会卡」；盲审不收敛=前期工作不够，但「方案已成熟+审核通过」也要敢进编码，不能无限打磨。

---

## 2026-09-13 · 表格交互与个人中心四批裁定

- **列宽拖拽全站+持久化（批17）**：EP v1 原生拖拽+TableShell 包装补 localStorage（colw.*）+双击回声明宽；v2 三表自实现手柄（v2colwidth）。业界调研（AG Grid/vxe-table/TanStack）裁定不引库——能力缺口 ~300 行自建补齐，迁移 2-4 周零收益。
- **列显隐=例外解非通用解**：仅「列数溢出且各列各有受众」的表配 ColumnSettings（现 18 张）；列少的表配了=空壳噪音。
- **列宽/显隐 localStorage 治理**：键名锚定语义 prop **永不再变规则**；禁自动清理/迁移代码；清档=给键名用户手动清（详记忆 colw-localstorage-policy）。
- **邮箱修改=验证成功才改**：不建 email_verified 列、不存 pending 新邮箱——确认前库中零痕迹，失败即无事发生（用户裁定）。
- **出站邮件封面名「人工智能开发学习平台」沿用**：既有低调封面设计，改邮箱/找回密码/邀请三模板统一，非站名「蜗牛量化」。

## 2026-09-03 · SF1 长尾清尾裁定：F-37/38/49 知情接受 + F-50/56 完成

- **F-37/38/49 知情接受（不修）**：F-37 停止延迟已从 direct 60s 缩到 hub 5s（send_order 无 stop_due 门控的残余窗口仅 5s，停止即 `os._exit` 进程整体终止）；F-38 last_price≤0 tick 静默丢是 B4「vnpy 同款」有意设计（停牌标的本就无 bar）；F-49 暖机靠 send_order 时刻 `buy_ok_check`（last_bar_wall<300s）+ hub 流回放间接缓解，`_warmup_history` 不显式校验新鲜度。三者低风险/有意，不再扩门。
- **F-50/F-56 本批完成（独立复核后不推迟）**：F-50 核心=order_log 加 vt_orderid 列（迁移 0063）+ write_trade_log 重启后 vt_orderid 反查（消除 order_id NULL 误判）+ 「委托不成交」口径收紧排除 send_failed；撤单 canceled 终态涉及 EVENT_ORDER 监听分层，不做（口径收紧用 status='submitted' 已隐含排除）。F-56 子问题 1=worker TD 独立 client_id（runner_client_id 派生 2-99，XTP 普通用户 1-99 见 xtp_trader_api.h:602）；**子问题 2（SDK 目录）判定不修**——MD 写 quote.log、TD 写 trade.log，文件名天然区分，目录级共享非并发冲突（子代理的「并发互写」系未复核的过度判断）。

## 2026-09-03 · 冒烟门抓修 3 真 bug + 令牌/密码/staging 若干裁定

- **canvas-var 走 cssVar 解析器**（用户裁定①）：批一 A 档把 echarts 图内色换 `var(--)`，但 canvas 不解析 var()（zrender 直传 fillStyle 静默丢弃）。裁定加 `utils/cssVar.js`（getComputedStyle 解析）替换全站 21 处，而非回退字面 hex——保住令牌单一源 + 暗色自适应。
- **staging fixture 撤销 mock 分支**（用户澄清）：本地「量化交易助手」（dev DB id=2）真实可用、走 webhook 路径；`quant-feishu-bot@2` 长连接单元重启 dwell 不稳。撤销 ws_client mock 分支 + 假 bot 种子，staging 波次回退 web/celery/hub 三波次（feishu 长连接凭证待诊断）。
- **令牌「不扩门 + 语义就近」**（用户裁定）：#999/#303133/#f8f9fb 等 EP 默认残留换语义就近令牌（接受变色）；令牌门**不扩** layouts；「绿=跌」只限国内蜡烛图，DataIntegrity complete `#67c23a`→`--success`。
- **生产密码=临时测试密码**（用户澄清）：生产 admin 密码是为测试设的临时密码（明文见 server-info 记忆，不落 repo）；环境变量化（SMOKE_PASS）后 repo 明文清零，无需改密码。

## 2026-09-02 · 告警订阅分发架构：三通道 Celery 队列化+notifications.dispatch 全程审计（用户三轮裁定+双盲审三轮）

- **订阅模型**：全局一套（alert_channel_sub 三行 im/email/sms），每通道独立类别多选+min_level 门槛（warn+ 可调）——取代旧 critical→discord 硬编码路由（channel_config webhook 链保留为过渡兜底，零订阅时回落，订阅配好自然失效）
- **异步铁律（用户裁定）**：推送必须队列化不阻塞业务——notify() 同步增量=一次 queue.put；发送全在 risk worker（alerts_im/email/sms 三队列，-c 1 与长任务隔离）或降级 daemon 线程
- **全程可审计（用户裁定）**：notifications.dispatch jsonb 回写——ok/queued/sending/failed:token/skip:token；{}=零外推终态,null=未跑完（死亡窗）——第四种"说不清"不存在；网页通知页 chips 可见
- **双发窗封死**：claim 认领式（queued→sending 单向迁移,rowcount=1 才发）——降级直发与 worker 只有一方获得发送权（短信计费敏感）
- **凭证面**：SMS 走 system_config 加密列（smtp 先例,Web 配零重启到位即通）；alerts_config 权限 admin 专属锁三处（analyst 有 system_config 不能触告警路由/计费面）
- **分层层级**：celery 任务定义归 scheduler(3)；alert_notify(2)→im_bot(3) 走 EXEMPT_UPWARD 成文豁免
- 详 docs/obsolete/任务归档/批7-告警订阅分发.md（三轮双盲审 49 条全吸收的完整契约）

## 2026-08-19 · 分钟数据源策略：XTP 自攒为主，Tusharestk_mins 产品包后启（用户拍板）

- **决定**：Tushare stk_mins 是独立产品包（2000 元/年），当前全局 1 次/小时不可用。池驱动分钟同步基础设施已建（`data_sync/pool_minute.py` + beat + 限速闸门 + API 端点）但** beat 禁用**——买包后取消注释 beat + `data_source_config.params` 配 `rate_limits` 即启用。
- **分钟数据路径**：XTP hub 订阅池标的自攒（影子期后写 bar_1min 正表），Tushare 将来作为主源、XTP 辅助校验。
- **复权因子**：已回填 95.4%（API 限速宽松），剩余 5% 部署窗口后续填即可。
- **关键实测**：stk_mins **全局** 1 次/小时（非 per-symbol）→ 30 只池日度增量需 30 小时，不可行。

## 2026-08-19 · 链条打磨：因子→实盘全链打通（四批，26 断点清零）

- **起因**：用户要求链条"打磨成熟、功能完善、体验良好+操作指导书"。探查实测 26 断点——4 整段断裂（自定义因子出不了 API 进程/因子模式实盘零下单/回测实盘频率+参数双错配/预检失败当成功）+2 UI 页失效+DSL 死功能。
- **关键决策**：①执行规则**方向感知**（R-F1：SELL=持仓口径 ALL_IN 清仓/BUY=可用资金——持仓走 ST2 position_snapshot 真相源）②DSL 实现而非删除（8 窗口函数+静默错值四形态抛异常——R-F2：错值比崩溃危险）③旧启停移除（LiveTask 唯一入口）④指导书分册+Web /help 内置都要⑤因子试算=写完即看曲线（真实 bar 喂 compute）
- **架构联动**：自定义因子三进程加载（web/celery/runner）+回测任务头 lazy 重载+factor:recalc 兼热重载钩子；全败 run 不过 F-44 验证门
- **指导书**：docs/manual 五册随 rsync 部署（server/docs/ 镜像），/api/help/{topic} + Web /help（marked 渲染）

## 2026-08-19 · 模块归位：quant_common 底座 + 分层断言测试（消 6 条层级违规）

- **起因**：用户质询"多轮修改后高内聚低耦合还成立吗"——依赖图实测 6 条层级违规，根因全是"共享工具/逻辑寄生错误位置"（crypto 在 web_api/时段工具在 runner/预算检查在 HTTP 入口/审计在 auth），非发散性腐化。
- **决定**：建 `quant_common`（层 0，crypto 纯函数/session/guard 回调注入/terms 注册表，白名单 cryptography+dotenv）；audit_log→data_platform；build_xtp_setting→strategy_framework/broker；check_budget_alerts→llm_gateway（随迁 notify 化——预算预警从直推企微改进站内铃铛）；email_service 独立模块。原址留 re-export 保兼容。
- **守门**：`test_layering.py` 4 断言（quant_common 纯度/层级禁上行含 lazy/历史违规边回归锁/第三方白名单）——分层从理念变测试，违规即 CI 红。
- **豁免登记**（唯一双向环）：data_platform↔alert_notify（tushare 同步告警，横向服务，回调注入为未来优化项）。
- **教训**：s.replace 无 assert=静默 no-op（hub_worker"直连"宣称不实被 Q 实锤）；py_compile+全绿测试都抓不到顶层双定义/静默未中的搬移——**assert 是搬移手术的必备缝合线**。

## 2026-08-18 · ST2 持仓真相源 + PUT→POST + schema 生成式基线（三联决策）

- **ST2（消 D2）**：持仓真相=券商 query_position 快照（`position_snapshot` 当前状态表：每批同事务 DELETE+INSERT，空批可表示空仓）+ `position_refresh` 心跳（stale≠空仓）。写入点=60s 循环取返回值（**否决** EVENT_POSITION handler——direct 模式 vnpy init_query 每 4s 常推会散批+15 倍量级）；TD 断线守卫内不写假空仓。trade_log 推导降级为 /api/reconcile 第四比对（归因）。生产实证 direction=Net——端点不过滤 direction。
- **PUT→POST 硬切（A 案）**：对齐业界趋势（Google AIP-136 等避免 PUT）。16 端点+前端 17+契约 19 处；**路由遮蔽教训**（参数化 POST 路由会吃后注册的静态路由）→ 4 静态路由调序 + 结构化顺序断言（test 锁全路由注册序）。结构化顺序断言模式可复用于任何路由变更。
- **#48 schema 生成式基线**：期望清单=迁移链 scratch 产物（schema_expectations.txt，**禁手写**——手写清单必腐有仓内实锤）；verify_schema=纯函数单向存在性（expected⊆actual），告警路由归入口层；四入口接线（web/runner/hub/celery 父进程）。**每加迁移必须重跑生成命令**（db.py load_schema_expectations docstring）并提交。
- 八段工作线（方案→审核→代码→审核→本地测试→部署→生产测试→提交）同日定型为默认流程。

## 2026-08-18 · S6 修订：断流不自杀 + 安全判定挪下单时刻 + 双层监控（内部 health_monitor / 外部 Zabbix@NAS）

- **决定**：①hub/direct 的 tick 断流自杀（300s os._exit）删除，只告警（文案带 runbook）；staleness 基线一律**时段作用域**（进入沿清零）。②BUY 安全判定从后台定时器预计算的 `frozen["now"]` 改为 **send_order 时刻事实检查**（`buy_ok_check`：bar<300s+hub 心跳），日历/交易所规则从动作路径清零；sticky 冻结（untrusted/gap 污染事实）保留。③监控双层：内部 `src/health_monitor/`（30s beat，症状型规则+沿检测+health_event 落库+自身心跳供外部反监）；外部 Zabbix server 装 **NAS**（常在线+自带通知），agent 装 quant 服务器，标准模板+systemd 插件+`/metrics` Prometheus 格式拉取。
- **为什么**：hub 每晨 09:31 必自杀（基线跨日污染，34627s 假断流）实证了"把交易所/平台节奏预期编进守卫触发器"必翻车；重启治不了平台/网络问题（只治进程自身），真僵尸态罕见且可观察，误重启每天发生——交换正确。暴露端对齐业界（/healthz /readyz /metrics=Prometheus 文本），不自造格式。
- **边界**：真"连接正常但数据不流"僵尸态改为响亮告警+人工重启（runbook）；后续可按数据加"长时间才重启"末档。
- **详见**：`docs/design/D15-服务监控设计.md`（设计+职责划分+runbook）；12 号 ST4 节已加修订指针。

## 2026-08-15 · N 语言架构约束：注册表驱动 + en 缺省

- **决定**：多语言设计为支持任意语言（当前实现 zh/en），英语为不匹配时缺省。所有语言相关逻辑改为**注册表驱动**（dict/array），不写死双语。
- **加新语言=只加条目零逻辑改动**：locales/index.js + i18n.js LANGUAGES + terms.py TERMS/LANG_NAMES + 邮件模板 dict。
- **各层策略**：页面=浏览器语言自动；条款=全语言纵向堆叠（不依赖检测）；邮件=操作者界面语言（请求传 lang）；LLM=跟随输入。
- **禁令**：写死 zh/en 二元判断、独立语言变量（TERMS_ZH）、硬编码语言列表。测试锁约束（test_new_language_only_needs_entry）。

## 2026-08-15 · 后端错误码化：字符串码 + 前端本地化映射

- **决定**：`ApiError(status, CODE, 中文兜省)` 响应 `{detail, code}`；前端 `apiErr(e)` 优先 `err.<CODE>` 本地化、无映射回落 detail。
- **否决数字码**（如 40001）：不自描述、要查表。
- 用户流程 22 处已迁移；深层管理接口增量补码（未映射自动回落安全）。

## 2026-08-15 · 末位 admin 保护：管理页移除 / 自助注销保留

- **管理页**：不可达（user_mgmt=admin-only + 不能动自己 ⇒ 操作者若是另一 admin 则目标非末位），删除死规则。
- **自助注销**：真实可达（用户对自己操作，不经管理页），`guard_self_deactivate` 单独设防（唯一启用 admin 不可注销自己）。
- **教训**：规则设计时验证可达性，不可达的规则是死代码。

## 2026-08-15 · 参考方案借鉴四批次（用户管理）

- **背景**：用户提供的邀请制用户管理参考方案，对比后 3 处原则冲突不借鉴（邀请预设 Trader / 超管分层 / 数字错误码），6 项借鉴分四批落地。
- **A**：禁用提示 / 邮箱登录 / last_login / JWT jti 黑名单
- **B**：邀请记录列表+撤销
- **C**：昵称+头像+Profile 页+顶栏改造
- **D**：软删除+脱敏+自助注销

## 2026-08-14 · SMTP 配置 DB 化（弃 .env）

- **决定**：SMTP 五项走 `system_config`（前端系统配置页可改），`.env` 不再参与。`SMTP_DEV=true` 仅本地显式开发模式。
- **理由**：单一真相源；.env 难改且易残留旧值（BASE_URL IP 问题的教训）。

## 2026-08-14 · 通知中心：类别×角色可见 + 仅实盘紧急外推

- **三项用户决策**：① 可见范围按类别×角色（email→admin 等）；② 外部通道只推 risk+critical（实盘紧急），订阅型 report 保留；③ 告警历史从 Valkey 直接切 PG（旧数据不迁移）。
- **行为变化**：磁盘/接口健康 critical 不再外推（仅站内）。

## 2026-08-13 · DDL 全部入迁移 + 运行时 DDL 清零

- alembic 迁移为唯一真相源；`verify_schema()` 启动校验；30 处运行时 CREATE TABLE 全删。

## 2026-08-10 · Codex 失效，Claude 直接做

- subcodex 复杂任务返 Ark 400（function calling bug），当前 Claude 直接编码，full-subagent 铁律豁免。

## 2026-08-09 · 自包含任务文档体系

- 新待办按 `docs/obsolete/任务归档/<id>.md` 写（8 字段），做任务只读任务文件+接口契约+模块契约，零代码阅读。

## 2026-08-08 · 平台化通用架构方向

- 6 大接口抽象（DataSource/Broker/MessageChannel/Task/RiskRule/LLMProvider），别人配置+实现接口子类即可接入，不改平台代码。

## 2026-08-07 · LLM 网关简化

- 移除 tier 机制（按 priority 全局排序）+ 移除语言注入（LLM 按输入语言自然回复）。

## 2026-08-03 · 策略+回测体系架构

- 四层（Strategy/Factor/SignalAggregator/Adapter）+ ActionSignal 契约 + 因子注册制 + 风控覆写 + DSL/Python 双模式统一执行。

## 2026-08-03 · 策略实盘化：修正版 B

- 每策略独立子进程（systemd quant-strategy@<id>）+ 独立 vnpy MainEngine + XtpGateway 实时驱动（tick→BarGenerator→on_bar）。

## 2026-08-03 · A 股进实盘开关（废止只读）

- AStockReadonlyAdapter 废止，A 股统一走 XTPAdapter；实盘三级开关 AND：.env 总闸 + Web 分项 + 策略级。

## 2026-08-17 · 实盘链路验证收尾（新架构定语义 + 运维硬化）

- **live_task 是新架构唯一运行语义**：runner 停止条件查 `live_task.status`（stop_live_task 置 stopped）；`strategy_config.enabled/backtest_verified` 只作创建/启动时的门禁。双 unit 归位：`quant-strategy@`（--id 旧架构）/ `quant-live-task@`（--task-id 新架构），polkit 两者都放行。
- **服务器加 2G swap**（1.8G 内存跑不动 runner 的 XTP 合约加载尖峰，全局 OOM 实锤）；runner MemoryMax=1G。
- **部署实例清单真相源 = DB**（feishu_config），不再"收集 active 实例"（会把误启的幽灵转正）；install-services 以 root 跑 + restart polkit + 输出 unit 状态快照。
- **当天验证结果**：tick→bar→on_bar→signal→risk→XTP order 全链通（证据在进展.md §0）；trade_log 写入缺失转 #46。

## 2026-08-17 · XTP 改共享行情进程架构（用户拍板）+ 稳定性检查方法论先行

- **架构决定**：XTP 侧改"共享行情 hub 进程（持有 XTP 连接+合约表）+ N 个轻策略 worker"，用实时性换内存（国内市场 tick 密度低，分钟 bar 足够）。与"修正版 B 每策略独立进程"的隔离性权衡：进程隔离弱化为"hub 单点 + worker 独立"，hub 稳定性要求因此**更高**。设计前必须先做稳定性需求书。
- **流程决定**：动手检查/改架构前，先审定 `flow/规范/稳定性检查方法论.md`（五轴枚举矩阵 + 四层检查手段 + 双盲交叉验收），检查按方法论执行，防经验式清单遗漏。crypto 侧维持独立进程（无内存痛点，纯 API 轻量）。

## 2026-08-22 · SECRET_KEY 根密钥方案（用户拍板）

- **问题**：需两个独立密钥 JWT_SECRET + ENCRYPTION_KEY，漏设一个就告警，JWT 轮换会孤儿化加密凭证
- **决策**：一个根密钥 `SECRET_KEY`，HKDF-SHA256 派生子密钥（`info=b"jwt"` → JWT 签名，`info=b"encrypt"` → Fernet 加密）
- **优先级**：SECRET_KEY（推荐，无告警）→ ENCRYPTION_KEY 单独设置（向后兼容）→ JWT_SECRET 单独设置 → JWT_SECRET sha256 派生（旧行为，告警）→ 进程内随机密钥（重启孤儿化，critical）
- **技术选型**：HKDF（RFC 5869，已含在 cryptography 库中），salt=None + info 域分离
- **迁移**：脚本更新为从 SECRET_KEY 派生新密钥，旧密钥仍从 JWT_SECRET 派生
- **向后兼容**：JWT_SECRET / ENCRYPTION_KEY 环境变量仍可单独设置，SECRET_KEY 未设时行为不变

## 2026-08-24：运行时韧性分层模型 + market_session 配置化

**背景**：开盘三验证暴露两大故障（hub 僵尸会话 + 任务 8 停机 2.5 天），暴露出系统缺乏统一的故障处理框架。

**决策**：
1. **运行时韧性分层模型**（L1 机器层/systemd / L2 会话层/进程内自愈 / L3 意图层/调和器）：
   - 退出/重启只属于进程域故障（崩溃/挂死/配置错）。数据流症状永不杀死进程。
   - 外部世界故障 = 进程内无限重试 + 有界退避 + 告警，无退出路径。
   - 已知周期失效用定时续航，未知失效用反应式重登。
2. **MdSession 契约**：行情会话生命周期抽象，引擎只依赖契约，平台领域知识全封子类。
3. **market_session 配置化**：交易时段从硬编码改为 DB 配置驱动，`set_config_provider` 回调避层层 0→1 依赖。
4. **SA4 重新定位**：只服务「进程真的死了」，不接数据流症状的 exit。
5. **告警通道未配**（行动项）：请 Web 消息通道页配 channel_config。

**替代方案**：零 tick 退出让 systemd 重启（跨层滥用，弃用）

## 2026-08-25 · L2 不加"升级退出"条款（用户裁定）

- **背景**：架构对标（20 号文档）发现 §2.8 两硬规则存在死角——进程活着但 SDK 状态毒化时（08-25 晨 3h 僵尸形态），L2 原地自愈久攻不下，规则一禁止退出，最终靠人 restart。曾提案 OTP intensity 式精化（L2 连续失败超阈值→升级 L1 一次）。
- **裁定**：**不加**。两硬规则保持绝对；活毒状态接受为已知残余风险，处置=告警+人工。理由：防重启风暴的确定性优先于罕见场景的自动恢复。
- **含义**：后续会话/批次不得再提议 L2→L1 升级退出路径。

## 2026-08-28 · 批 6 跳过 ST7 影子门禁、阶段 1 先切换（用户裁定）

- **背景**：原计划 ST7 门禁 ≥4/5 干净日→批 6 阶段 1。观察日三查揭门禁假绿+双轨差异四分类；tcpdump 包级实证仿真平台对两条 MD 连接推送不一致（1616B 快照包 373 vs 119，方向翻转）——零差异门禁在仿真环境结构性不可达，容差口径决策悬而未决。
- **裁定**：**先切换，周一生产验证**（hub 模式运行验证代替影子门禁）。依据：①hub 输入质量实证优于 worker-MD（25 vs 8 快照/分）——切换=信号源数据质量提升；②15:01 竞价分钟两侧逐位一致证明等价性可达；③门禁口径决策成本>收益，切换后该决策消解。
- **配套**：任务级+全局 md_mode 双切（盲审 B-P1-1 防混态）；配置级秒级回滚（改回 direct+restart）；direct 代码保留至 6b 验证绿后退役；周一验证清单（首根消费/TD 登录确认/gen_jump 误导告警勿动作/metrics 标签）。
- **同期裁定**：三查两站制（09:31 查昨日全量+15:10 查今日全量，门禁以盘后为准）+ST7 计数作废重置。

## 2026-09-08 · 批9 内存治理三裁定

1. **zram 而非合并 worker**：风控 worker 独立是告警 SLA 的刻意隔离设计（记忆 alert-dispatch-architecture），不为省内存牺牲进程隔离；OS 层治 OS 层的问题（zram+swappiness），zram 配置入 INSTALL.md §2.4 成版本化 runbook。
2. **platform.py 改名 _platform.py（「改就彻底改」）**：三层同名占用 import 机制保留地是历史反模式，懒加载暴露绑定竞态；治本=退出争议名，守门测试固化三不变量。约定：模块名单例实例同名+包级同名 re-export 禁止再犯。
3. **前端失败语义按页分型**：无独立 catch 的 load 用 Promise.all（全成或全败）；有独立 catch 的（Logs/Strategy.loadExtra）用分立 promise 保部分失败容错，禁裸 all 短路。


## 2026-09-15 · 批21-23 三批裁定

1. **圆钮配色规则**（批21）：图标按钮一律浅蓝灰底（--el-fill-color-light）+品牌蓝图标+hover 浅蓝；颜色只表语义（danger 红/warning 橙/success 绿）；primary 蓝退出图标按钮；每卡=告警源+状态，不产生告警的信息不监控（zram 因此不入）。
2. **告警/指标两张表一个循环**（批22）：health_event=沿事件（触发/恢复有状态），system_metric=连续采样（趋势）；资源阈值 severity 存 Valkey state 键值（warn→critical 升级重发、降级只换徽标）；disk 逐挂载点判定（聚合稀释单分区爆满是漏报根因）。
3. **通知退回外部推送+留档**（批22/23）：系统 UI 只盯告警（铃铛=活跃告警数角标 admin-only）；通知中心确认机制退役（active 池只增不减，全清是唯一清空手段）；日志（task_logs）不做删除——实盘任务时间线是自愈数据源。
4. **表头位置规则**（批23）：卡片 header 左=标题，右=筛选+动作组（统一序：筛选→新建→刷新→列设置）；行筛选统一 RowFilter 弹窗复选多选（「全部」=空数组）。


## 2026-09-23 · 多账号源 D1-D6 双盲审修正（双同 PASS，13 P1 全修，3 条触首决细节）

- **双盲审结论**：双同 **PASS（无 P0）**——软件架构专家 + 量化交易高手，同章程互不可见，主会话同判比对。13 P1 + 8 P2，性质=契约遗漏/接线不明确/枚举不全/措辞失实，**无需返工**。按 [[stop-and-question-when-review-loops]] 教训不走「盲审→补缺陷→再盲审」循环，**列全真问题→一次性改→自查无矛盾**。
- **3 条触首决细节的修正**（盲审发现 + 已逐条查码核实，落 D 文档；待用户确认）：
  1. **资金基线口径**：D2-3「显式配置」→「per-account 首条快照 total_value（跟踪起点净值），显式配置仅参考展示、不作回撤分母」——回退到名义入金数会重演 9.99 亿回撤分母错配（2026-08-22 #10 口径已修过）。
  2. **account_key 唯一作用域**：单列 UNIQUE → `UNIQUE(provider, account_key)` 复合唯一（防跨券商资金账号撞号）+ 加密 account 取 API uid/自定标签（加密无资金账号）。
  3. **`_market_of` 不退役**：31号§四 曾把 `_market_of`（分项 market_op 五键）与 `_board_of`/`detect_category` 混为一谈；三者三维度，`_market_of` 是分项（etf 分项=场内基金全体、perp 需 account.provider），退役即下单闸断裂。仅退役 `_board_of`/`detect_category`。


## 2026-09-23 · 吸收外部架构两可借鉴点（用户裁定）

- **背景**：用户提供外部「量化交易系统核心架构设计规范」（机构级通用架构），比对后两条可借鉴——①品类差异化操作插件化 ②交易所/股东户维度显式建模。
- **裁定（吸收进方案）**：
  1. **品类操作插件（预留扩展点）**：可转债转股/回售、ETF/LOF 申赎等品类专属操作当前不实现，但架构预留「品类操作插件」扩展点——品类差异=数据列（属性）+插件（操作），未来加品类/操作不改顶层代码。31号 §七 从「出界」改「预留」。
  2. **exchange 股东户维度**：权限模型三维（category/board/ST）→**四维（category/exchange/board/ST）**。`security_master` 加 `exchange` 枚举列（shse/szse/bse，回填从 vt_symbol 后缀提取，可靠非前缀判断）+ `account_permission` 加 exchange 维度。根因：board=main 横跨沪/深，单 board 维度分不出「有沪股东户无深股东户」的 account。
- **不采纳**：「自动路由层」（与我们「人工显式选源、一策略一账号」方向冲突，31号 §1.1 已钉死）。


## 2026-09-23 · ST 维度降级 + detect_category 就地退役（用户裁定）

- **ST 降级**：30号/31号 原把 ST 列为「第四维正交权限」，用户质疑「为什么要关注 ST」→ 澄清三层（涨跌幅=`band_rules.pct_st` 已覆盖 / 主板 ST 适当性=唯一真权限但薄 / 退市风险=选标的排除）。**裁定：ST 从独立正交维度降级为「board=main 子布尔」**——account_permission 加 is_st 布尔（仅主板），非第四维。
- **detect_category 就地退役**：detect_category 返回 astock/crypto（因子兼容词表），与 security_master.category（stock/perp）两套词表打架。**完美方案=品类单一真源 + 因子类=派生映射**（`CATEGORY_TO_FACTOR_CLASS`: stock→astock、etf/fund/reits→etf、convertible→convertible、perp→crypto）。detect_category 仅 1 调用点（factor.py:768），就地退役成本小，纳入 D1（与 _board_of 同「前缀→数据列」模式）。


## 2026-09-24 · D1 代码双盲审 B-P0 裁定：写路径/三级时点/回填闸挂账 D5

- **背景**：D1 代码双盲审 B（交易正确性）抓 P0——D1 §五「只做」列「account_permission 读写」+「三级时点接入调用」，但实际交付只有「读」（account_allows 读）+「函数」，写路径（管理端点/seed/UI）与三级时点接线均未交付。空表 + 无写路径 = 若接线即全盘锁死。
- **裁定（挂账，非返工）**：①account_permission **写路径** ②**三级时点接入调用**（依赖 D2 的 live_task.account_id）③**board 回填完整性闸**（接线前断言 stock 全量 board 非空）→ 归 **D5**（建任务与 worker 绑定）前置；④**ST 官方名单 fail-closed** → 另批（现 namechange 派生、无档=非 ST fail-open 已知）。已落 D1 §五 + 待办。

## 2026-09-26 · 批 66a 三裁定（D26-B 切换前收束）

1. **concrete unit 整文件遮蔽替代 drop-in**（编码裁定，回写任务文件 §2.2）：install-units wrapper 只收 `*.service`（文件名正则），drop-in `.conf` 过不了安装通道——过渡注入=仓内 concrete `quant-md-hub@quant.service` 遮蔽模板实例化+`__HUB_ROW_ID__` 占位符经 release.yml 阶段 2 replace 按 inventory 注入（staging/prod 行 id 不同不可写死；replace 在指纹采集前=值变化触发单元重装）。禁入共享模板立法不变（A-P0-2 加密实例污染事故链）。66b 换名后三件套整体退役。
2. **66b 观察专项挪周二 09-29**（盲审 A-P2-1）：周一窗已承载批 64/61/65/63P4 四批+66a 顺带，第六改动同交易日归因困难——66a 顺带观察周一、66b 键切换专项周二。
3. **凭证对账读行失败=版本顺延不固化**（双盲 A-P1+编码自查同构洞）：任何「读行失败但固化新版本」的写法都会短路死锁凭证基线（首发失败固 v / 版本轮失败返新 v+旧摘要两洞同构）——统一语义「读行失败=本轮没发生，版本顺延下轮重读」；staging/prod 行 id 填值防呆注释入 inventory（勿填 EMT 行）。

## 2026-09-27 · 批 66b 三裁定

1. **mask 弃用+concrete 残件保留**（双盲 A/B P0 同判）：systemctl mask 对目标位实体文件必败（/etc concrete 残件=66a 装位，实测 rc=1）且 mask 断回滚复活承载件——旧 @quant 退役收敛 stop+disable，防复活由新代码 ACCOUNT_ID 断言 78 Prevent 兜底（残件以新代码启动即拒）。
2. **EMQ 连接窗=hub 内部状态机（用户裁定）**：柜台时段性失败不下沉为 deploy 闸门豁免——网关层 `_emq_window_open`（交易日 8:25-15:15，东财 FAQ Q12「8:35 起服务」lead 10 分）+窗外 defer_login（api 已建不试不报）+poll_supervise 窗开沿重登（60s 节流）；窗内 Login 失败照旧 78。对齐 XtpMdGateway 先例/D26 §3.3「连接生命周期=hub 内部状态机」立法——「知道不该试」优于「失败不报」，dwell 闸门零改动。
3. **回滚三块形态闸**：hub 换名回滚专属块（停新 unit/复活 @quant/清单覆写）仅当回滚目标 release 树含 concrete unit（=66a/更早形态）才执行——防 66b 后回滚（66c→66b 等）被无条件拆掉恰需的数字 hub。

## 2026-09-30 · 部署管道两个结构性缺口立项（批 84/85——用户裁定「立」）

- **缘起**：批 83a 拆表过渡期对 prod 只读实查时，照出两条**与 83a 本身无关**的缺口。用户裁定**拆两项独立立项**（不裹进 83a）：84＝管道不许撒谎（工程诚实性），85＝迁移不许违规（纪律立法）。
- **批 84 · 回滚诚实性**：`rollback-tasks.yml` 无 alembic/downgrade → **回滚只回代码**；其「回滚复验」仅两项＝版本收敛（`:138`）+ `/healthz` 200（`:163`），而 `/healthz`＝**liveness「不查依赖」**（`web_api/routes/system.py:162-164`）、`/readyz` 升级也不够（只查 PG/Valkey **可达性**，`:167-179`）→ **「旧代码 + 已删表」这条撕裂态在整条复验里完全不可见**，管道照旧输出「已自动回滚」（`release.yml:585`）。且 `release.yml:582` 自称「schema 已前进不回滚——**expand-only 前提经 DDL 门保证**」，而阶段 4 门（`:315-350`）只认 `-e allow_contract=true` 即放行、**不校验是否真 expand-only**；该标志只置 `auto_rollback_disabled`（`:349`）、**仅被 rescue(2-5) 消费**（`:385`/`:392`），**rescue(6-8) 不看它**——即那句「保证」在门里**尚无实现**（自我陈述与实现的落差）。
- **批 85 · 破坏性迁移两步走立法**：把 expand → 双读双写/回填 → contract 从口头前提写成 `flow/规范/` + 迁移文件头声明（`# EXPAND-CONTRACT: phase=<expand|contract> pair=<id>`）+ 新闸门（无声明/不成对/孤儿/白名单反向校验）+ 既有违规迁移（`0116` 等）逐条处置结论。
- **执行前待裁定三项**（已写入任务文件与待办）：① 批 84 探针方案 **A**（`deploy/compat/<release_id>.json` + `quant-hbcheck compat`，**推荐**）vs **B**（`/readyz` 加表存在性）；② `auto_rollback_disabled` 语义一致化取 **(i)+(iii)**（禁用即整轮不自动回滚 + DDL 门拒绝 contract）还是仅 **(ii)**（最低限度诚实：改文案+打印实际版本）；③ 批 85 现有破坏性迁移逐条「补两步走 / 有意识接受并标遗留」。
- **本次未动**：零代码改动、零迁移改动、未碰 `deploy/`。任务文件：`flow/任务/批84-回滚诚实性与schema一致性.md`、`flow/任务/批85-破坏性迁移两步走立法.md`。

## 2026-10-01 · 批 85 遗留项裁定：id 跨域撞号「认债不还」（用户裁定）

- **裁定**：两新表（data_source / trading_account）独立 BIGSERIAL 序列的跨域撞号设计债**接受、不还**——不做 0123 共用序列迁移。待办与批85 文件遗留项 1 封账。
- **理由（用户原话口径）**：现处开发调时阶段；等第一个真正实用版本发布，甚至打算把历史整体删掉 ⇒ 「降级合并回单表」这条纸面路径没有实际兑付场景。
- **防线保留**：0122 downgrade 对撞号**响亮拦停**（须人工处置、不静默丢行），已在产——真要降级时会被拦停并人工处置，而非静默出错。
- **本次未动**：零代码、零迁移、未碰 deploy/；仅真相源三处登记（待办 / decisions / 批85 任务文件）。

## 2026-10-01 · 批 76 三裁决点（用户「按建议走」）

- **① unfreeze 档位**（**同日二次修订**，见下条）：~~IM 新工具 `task_unfreeze` 进 ADMIN_TOOLS + 新 perm 键 `unfreeze`（perm_registry api 13→14 键）；Web 端点挂**既有** `strategy_control`（与启停任务同页同键、零注册表面）。~~
- **② IM 验证通道**：**Web 确认链**——IM 发起（确认卡闸：身份+档位+去重）→ 回执带一次性 token 的 Web 深链 → 管理员在 **Web 登录态**确认 → token 一次性消费后写解冻请求键。**明文密码禁进聊天记录**（IM 只当唤起面）。
- **③ 临停误冻结归类**（66c 挂账）：归 `ts_gap` 同型**人工解**（零代码改动）——临停=市场真实洞与数据故障数据面不可分，宁多一次人工确认，**不自动放行**；冻结事实仍落 F1 可回看。
- **副产品裁定口径（沿用现状不另立）**：seq_gap 冻结的**自动解冻**由「衔接判定」驱动（本根未触发 ts 缺口且续上序号）——安全性来自判定标准是客观流连续性，非进程自证。

## 2026-10-01 · 批 76 裁决点①修订：解冻档位统一 `strategy_control`

- **裁定（用户）**：「统一成 trader 和 admin 都可以解冻吧」。
- **落地**：Web 端点（本已 `strategy_control`）/ IM 卡片确认面（原 `unfreeze`）/ LLM 聊天工具档（原 `unfreeze`）**三面同键** = `strategy_control`；`unfreeze` 注册键**退役**（API 键 14→13，零新注册表面）。`UNFREEZE_TOOLS` 常量保留（独立集=「不复用 halt/resume 档」的域语义不变），仅判定键改。
- **理由（保留原裁决点 a 的正确部分）**：① 与启停任务**同页同档**——同页的「启停/删除/解冻」是同一管理动作族的既有边界；② 消除「同一操作两入口不同判」的**同型债**（P1 教训正是同键才是对）。
- **连带（退役一键=五处）**：`perm_registry` 13、`test_batch33b_perm_registry` 三处断言、前端 `permGroups.js`、`locales.key_unfreeze`、`test_gateway.py` 原钉按新裁定反转；三面同键加**跨文件契约钉** `TestToolGatingContract`（从 ws_client 与 trading.py 源码抽键字面量互比，反证已做）。
- **副作用（有意接受）**：`analyst` 亦持 `strategy_control` ⇒ IM/LLM 面也可见解冻工具。不比现状更松——analyst **本就可 Web 调启停与解冻端点**（同键），原 IM 档反造成「Web 可、IM 不可」的不一致；要挡 analyst 须动 `strategy_control` 的角色分配（启停任务同款独立议题）。

## 2026-10-01 · 批 77 · 权限矩阵与实盘面分离（用户裁定「拆键 + 一并复核写进立法」）

- **缘起**：批 76 二次裁定把解冻统一到 `strategy_control` 后，用户提出**基础原则**：
  「**analyst 只能进行回测和实盘测试，不能执行实盘交易**」，要求**据原则推演**（非据现状）
  analyst 是否该有解冻权限。推演结论＝**不该**，且暴露出 `strategy_control` 键**粒度太粗**：
  一键同时管「研究面（写策略/因子/回测/验证，只写资产不起进程）」与「实盘面（起停 systemd
  进程/解冻）」——analyst 持该键＝拿两域＝越界。
- **裁定**：① 新增 **`live_control`** 键承载实盘面；analyst 保留 `strategy_control`（研究面）。
  ② trader 边界**一并复核并写进立法**（权限矩阵 + 新增动作归类判据）。
- **推翻前一轮**：批 76 二次裁定（统一 `strategy_control`）被本批替换——当时理由「与启停任务
  同页同档」把**现状**当**依据**；本批按原则判「启停实盘任务＝实盘动作，不该与写策略同键」。
  `unfreeze` 键仍退役（API 键 13→**14**，净增 `live_control`）。
- **落地**（详见 `flow/任务/批77-权限矩阵与实盘面分离.md`）：
  - **HTTP**：`trading.py` 6 端点（create/start/stop/delete live-task + unfreeze + unfreeze-request）
    + `strategy.py` 2 端点（start/stop strategy）从 `strategy_control` → `live_control`。
  - **角色集**：`trader` **显式 +`live_control`**（否则拆分削掉其原经 strategy_control 拿到的实盘
    起停权＝功能回归）；`analyst` 字面量不变但**语义净化**为纯研究；`admin` 自动派生零改动。
  - **IM**：`_need` 三工具（`task_unfreeze`/`strategy_start`/`strategy_stop`）→ `live_control`
    （修前更早状态是策略启停落 `trade`＝谁有下单权就自动能起进程）。
  - **LLM**：新 `LIVE_TOOLS`（strategy_start/stop + task_unfreeze）由 `live_control` 放行；
    `TRADER_TOOLS` 缩到仅 `emergency_halt`；`ADMIN_TOOLS` 仅 `risk_resume`；`UNFREEZE_TOOLS`
    保留为 `LIVE_TOOLS` 子集别名（旧裁定点 a 的域语义不变）。
  - **前端**：`permGroups` `live_control` 入 trading 组；`locales` 双语词条；`MainLayout` 新增
    **动作级权限注入** `provide('canPerm', k => perms.includes(k))`；`LiveTask.vue` 起停/解冻/建/删
    按钮按 `canLive` 显隐（此前仅 `navReadonly`，analyst 看得到按钮点了报 403＝死胡同）。
- **不动**：`live_trading_control`（市场级实盘总闸）**不并入** `live_control`——粒度不同
  （全市场闸 vs 单任务起停），并入会把两个粒度压平。
- **三面同键闸门**：`tests/test_batch76_unfreeze_surface.py::TestToolGatingContract` 8 钉；
  **逆转测试三重实证**（gateway 键、IM 键、HTTP 端点各逆转一次，分别红 2/2/3 钉）——证明闸门非恒绿。
- **验证**：全量 pytest **1798 passed / 2 skipped**；`ruff check src/` 绿；前端 build 绿。

## 2026-10-01 · 批 77 续 · 全站/全市场闸收回 admin 独占（用户裁定「halt/resume 都只挂 admin」）

- **裁定**：`halt`（全站熔断）、`live_trading_control`（市场级分项开关）**都只挂 admin**。
  `resume` 本就仅 admin（trader 集从无此键），无需改。trader 保留 `live_control`（单任务粒度）。
- **判据（粒度分档）**：**越粗的档越危险，持有者越少**——
  单任务 `live_control`（trader+admin）→ 全市场 `live_trading_control`（admin）→ 全站 `halt`/`resume`（admin）。
  且「开」比「停」危险，与既有 `resume` 仅 admin 同源。
- **落点性质（重要）**：本次改的是**缺省种子** `perms.PERMISSIONS`。**线上真相在 `permission` 表**
  ——用户已在界面删除 trader 的 `halt` / `live_trading_control` 行，线上行为即刻生效。
  改种子的目的是防**新环境**（表空回落字典）复活该权。**缺省收权 ≠ 堵死路径**：将来若需
  「实盘运维」角色，建显式角色赋键即可（非 admin 角色是字面量子集，加键自由）。
- **关键连带（同型病新形态）**：`llm_gateway._filter_tools` 修前条件
  `if "trade" in perms or "halt" in perms: allowed += TRADER_TOOLS` —— **`halt` 靠 `trade` 连坐放行**。
  若只改 `perms.PERMISSIONS` 而不动此处，「收回 halt」是假的（trader 仍有 `trade` ⇒ 仍拿到
  `emergency_halt`）。已改为 `if "halt" in perms:`。**教训**：批 76 P1 是「同动作多**面**各判各的」，
  本条是「同动作多**键**连坐」——同一个病的两种形态，收权时必须同时查**连坐条件**。
- **前端连带**：`Risk.vue` 三按钮补权限显隐（熔断 `halt` / 恢复 `resume` / 市场开关
  `live_trading_control`）。此前该页**完全无权限判据**（连 `navReadonly` 都没接）⇒ 任何进得来的
  角色都看得到按钮、点了才 403（批 76 P1 同型死胡同）。
- **IM 面无需改**：`_need` 里 `emergency_halt` → `"halt"`，键本身收回 admin 后**自动只放 admin**
  （IM 判的是该用户角色权限集，用户口径「按归属角色授权」）。这印证 `_need` 只是「问哪个键」，
  真正的判定由角色集决定。
- **闸门**：`TestGlobalGatesAdminOnly`（5 钉，含前端按钮显隐钉）+ `test_filter_trader_no_halt_no_resume`
  （**反转**原钉，非删除）+ `test_filter_trader_halt_requires_halt_key_not_trade`（连坐守卫）。
  **双逆转实证**：① 加回 `trade` 连坐 → 连坐守卫红；② `halt` 加回 trader 集 → 缺键钉红。
- **验证**：全量 pytest **1805 passed / 1 skipped**；ruff 绿；前端 build 绿。未上产（与批 77 主体同行）。

## 2026-10-06 · 同步窗口边界模型与参数分层（用户裁定「② 假地板无意义；加标的产生时间；冻结删掉、统一走边界模型」）

**背景**：OKX 首跑 7/485 标的缺 19 天不自愈（`_crypto_window` 首跑窗 30 天 > `_PARTIAL_FREEZE_MAX_DAYS` 10 天下限；且冻结 2026-10-06 20:21 才上线、晚于 102b 下午首跑）。由此引出「同步窗口该由什么决定、参数该归哪一层」的逐轮讨论。

**裁定**（用户逐轮拍板）：

1. **窗口 = 三边界交集**：`[ max(inception, retention) , 源上限 − 发布滞后 ]`。**两个地板语义不同、都显式化取 max——不用一个冒充另一个**（`first_run_days=30` 正是坏样本：既非真 inception 也非有依据的 retention）。
2. **`inception`（标的产生时间）＝每标的事实**，从列表快照派生。**非新建**——`security_master.list_date` / `engine._get_list_date` 已存在且已用，只是**仅用于单标的修复路径**；本次**升格为期望集定义**（存量资产，非新造）。
3. **参数三层归位**：换任务才变 → 定义层 DB；同类共享 → 代码按 kind 默认；连某源才需 → 接入层。**能力/列形状留代码（绝不进 DB，进 DB 即双真源漂移——批 107 `etf_list.pg_table` 同族教训）**；连接值/凭证进 DB。
4. **四条启发式收编**：`_PARTIAL_FREEZE_MAX_DAYS` **删**、`_TIER1_OVERLAP_DAYS` **删**、`_CRYPTO_TAIL_GRACE`+`_TIER1_LAG_TRADING_DAYS` **并入源边界**（发布滞后是源属性、非 kind 属性）、`first_run_days`/`default_days`/`20050408` **换 `max(inception, retention)`**。
5. **冻结删除是收尾步、不是起点**，**门控**：crypto perp 生命周期落地 + 对账在 prod 证明覆盖同一缺口。

**关键理由（为什么不能先删）**：

- **粒度错配（冻结缺陷的本质）**：游标是 **sync_id 级单值**、失败是 **symbol 级** ⇒ 485 永续里坏 1 个、整族游标就退回、下轮全族重拉；10 天界只是给这个「全族重拉」封顶防 ratchet。对账搬到标的/日期粒度是根治。
- **依赖倒置**：inception 是**派生数据**（来自 `asset_static_info`/`security_master`，本身是快照族同步项）⇒ 时序族完整性依赖快照族新鲜度；快照 stale 须判「**不确定**」而非「无缺」。同族前科：清单检测点不看 `enabled`（P1-A）。
- **只对 per-symbol 族有效**：per-date 整市场族（`astock_daily` 走 `pro.daily(trade_date=X)`）上游只返当日存活标的 ⇒ lifecycle **不产生缺口**，其洞是**整日失败**，须靠**日期级差集**；lifecycle 对它无效。**minute/pool 族才是正解，两类分开治。**
- **crypto 空白**：`security_master` 无 crypto 行 ⇒ 生命周期对 crypto 为空、对账空转；**不补 perp 生命周期就删冻结 ＝ 重开 OKX 那类洞。**
- **修正前论**：此前曾说「冻结与边界模型并存会**语义打架**」——**过头**，实为**冗余非冲突**（都幂等）；真问题是粒度错配 + 假愈合错觉（「愈合了最近 10 天」≠「已完整」）。

**文献**：`flow/方案/同步窗口与参数分层-设计.md`（设计定稿）。
**状态**：设计定稿，实施未开始；待裁点（crypto 生命周期来源 / retention 落地形态 / 对账调度频率 / per-date 优先级 / 孤儿参数键闸门）见文档 §十。
