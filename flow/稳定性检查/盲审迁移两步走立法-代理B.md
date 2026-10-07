# 盲审 · 批 111 迁移两步走立法（代理 B）
> 基线 commit 355e0c9

## 总评
不过门。设计方向（声明驱动 + 不可旁路门）正确，但**两个致命硬伤使其无法按稿实施且会静默放行**：①「现状#5 共 16 个破坏性迁移」是假数，且与正文/处置表/legacy/冻结白名单/实测五向不齐，而门④冻结的 `_LEGACY_EXPECTED` 必须精确等于实测升级段破坏性集合，否则①或④必然误红，验收"pytest 全绿"不可达（设计不可实施）；②同一套破坏性 op 判定表实现两遍（pytest/python + 部署/bash），无单一真源、无一致性校验，且两门已对"孤儿 expand"行为分歧（pytest 放行 / 部署硬拒），任一边漏加新 op 类即"看起来对、实际不拦"。这两点不修即返工。

## 致命（P0）
- **P0-1 破坏性迁移计数失实且五向不齐**：现状#5 正文写「16」，列表给 17 个（含 0095，而 0095 仅 `SET DEFAULT` 无破坏性 op），处置结论表 17 行，legacy 行 15，门④冻结 `_LEGACY_EXPECTED` 称「14」。我用设计自己的扫描法（`awk '/^def upgrade/,/^def downgrade/'`）实测：窄正则（对齐现状#2 门正则）命中 **13** 个、宽正则（补 RENAME/drop_constraint/rename_table/DROP CONSTRAINT/exec_driver_sql）命中 **29** 个。门④要求冻结集与文件内 legacy 标记互为子集，门①对"命中破坏性 op 且无声明"红。若按稿中任一数字冻结，将出现：未声明存量破坏性迁移→①红，或多余/陈旧 legacy 标→④红。**须以实测集合重算白名单，稿中 16/17/15/14 任一数字均不可用**。

## 严重（P1）
- **P1-1 双真源 + 两门不一致**：破坏性 op 判定表实现两遍（产出4 pytest / 产出5 bash 正则），无单一真源、无 parity 校验。已现分歧：pytest③ 孤儿 `phase=expand`「进报告不红」，部署门（产出5）对非 `phase=contract` 的破坏性迁移一律 `rc=1` 硬拒 ⇒ **本地绿 ≠ 部署绿**；且新增一类破坏性 op 时两边任一漏改即静默放行（正是最危险类）。
- **P1-2 声明真实性零校验**：门只校验"声明存在"，不校验真伪。新破坏性迁移标 `legacy`（不在合成集时④本应红，但新文件标 `phase=expand pair=<未来/不存在 id>`）即可过 pytest（③孤儿不红），RENAME 事故式"门看不见"再现。部署门虽严，但仅靠自申报，无独立审批钉。
- **P1-3 逃生门完全不设**：生产事故热修需单步破坏性 DDL 时，部署门 `rc=1` 硬拒、无任何受控审计逃生；被迫改门留痕反而制造更不可控的漂移，与"治假门"初衷相悖。
- **P1-4 `$prev` 路径脆弱**：部署门 `pair` 须 ∈ `$prev/migrations/versions`（`release.yml:365` 的 `readlink deploy_root/server`）。首次部署无 `$prev` ⇒ 任何 contract 被误拒；同发布捆绑 expand+contract 时 pair 不在 `$prev` ⇒ 误拒（虽契合"两发布"意图，但稿未写明此行为）。

## 一般 / 陷阱核对
- **扫描法漏网**：`op.drop_constraint`、`op.rename_table`、`ALTER COLUMN … DROP NOT NULL`、多行拼接 `op.execute("DROP TABLE " + "x")` 均不在 `awk+正则` 内（drop_constraint 系设计有意排除，其余为盲区）；RENAME 漏网设计已承认并将补。裸 SQL `DROP TABLE/COLUMN` 与 `exec_driver_sql` 可被窄正则捕获。
- **头声明置顶安全**：逐文件核 141 个迁移，首行**全部**为 `"""` docstring，无 `from __future__`/编码声明/BOM ⇒ 加 `# EXPAND-CONTRACT` 第 1 行注释不破坏 `__doc__`（注释非语句），置顶可行。
- **现状#1/#2/#3/#4/#7/#8/#9/#10 均属实**（见下表）；#6 基本属实：0116 `upgrade`(77–152) 仅 `drop_constraint`×2(142,155)，按本稿窄定义非破坏性；0122 `upgrade` 确有 `DROP TABLE external_interface`(:48)。
- **rescue(6-8) 当前 `when` 仅看 `prev_release_id`**（`release.yml:624-626`），不含 `auto_rollback_disabled` ⇒ 产出6 补合取项前提属实。
- **制度冲突**：未发现与 D18 §2.3 / 八步法硬冲突；D18 仅加一行指针，措辞落地核对即可（一般）。

## 事实核查结果表
| # | 设计稿断言 | 核实结果 | 证据(file:line) |
|---|---|---|---|
|1|阶段4门被 `allow_contract` 跳过|真|`release.yml:391` `when: not (allow_contract\|default(false)\|bool)`|
|2|正则只认6类、RENAME 不在内|真(窄)|`release.yml:380` grep 无 RENAME|
|3|2026-09-24 RENAME 事故门看不见|真|`flow/踩坑记录.md:65`|
|4|零 `EXPAND-CONTRACT` 声明|真|全仓 grep 仅命中 flow 文档/本稿，无迁移文件|
|5|破坏性存量共 16 个|**假**|窄扫描13/宽扫描29；正文16·列表17·表17·legacy15·冻结14 五向不齐|
|6|唯一两步走=0116↔0122|基本真|0116:142,155 仅 drop_constraint；0122:48 DROP TABLE|
|7|0095 `SET DEFAULT`、门放行|真|`0095:6,19`|
|8|`allow_contract` 仅3处:391/393/396 无默认|真|`release.yml:391,393,396`|
|9|S2 以"破坏性 DDL 命中"判据|真|`run_scenarios.sh:241`|
|10|jsonb 测试=静态扫+枚举+白名单+反向+反证|真|`test_jsonb_columns_guarded.py:1-57`（_EXEMPT 反向校验、_LAWS、PG 实测）|
|产出5|`$prev` 来自 `readlink server`|真但脆弱|`release.yml:365`|
|产出6|rescue(6-8) 未看 `auto_rollback_disabled`|真|`release.yml:624-626` when 仅 `prev_release_id`|

## 对待裁点结论表
| 点 | 判定 | 理由 |
|---|---|---|
| 双实现(pytest+bash)是否可接受 | 有条件同意 | 接受两门存在，但须单一真源（共享判定清单 / bash 由 python 生成）+ parity 一致性测试，否则拒绝（漂移=P0 级风险） |
| 逃生门要不要 | 不同意完全不设 | 不设无痕旁路 ✓；应加受控审计逃生（incident ref + 强制日志），否则真事故被迫改门留痕更不可控 |
| 16 条裁定是否认可 | 部分同意 | 只认 0116↔0122 一对的方向同意；legacy 集合须以实测破坏性集合重算，不接受稿中 16/14/15 任一数；裁定理由诚实（未虚构配对） |
| D18 §2.3 加指针 | 同意 | 互补不冲突，措辞落地核对即可 |
| 头声明第 1 行 | 同意 | 逐文件核安全（均 docstring 首行，无 BOM/__future__） |

## 过门判定
**不过门**（须修 P0 + 指定 P1 后复审）。必修清单：
1. **P0-1**：以实测 upgrade 段破坏性集合重算 `_LEGACY_EXPECTED`，消去五向数字不齐；附扫描脚本与输出作为评审物。
2. **P1-1**：设单一真源 + parity 测试（两门对合成语料同结果），消除"孤儿 expand"行为分歧。
3. **P1-2**：legacy/phase 声明加真实性约束（新文件标 legacy 须不在合成集，或加审批钉）。
4. **P1-3**：受控审计逃生（非无痕旁路）。
5. **P1-4**：写明 `$prev` 缺失 / 同发布捆绑 expand+contract 时的明确行为。
