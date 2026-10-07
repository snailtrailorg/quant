# 复审-代理A · 批111 迁移两步走立法（步2 复审轮）

> 独立盲审员 A，互不可见；结论均来自本人对代码/DB 的复核，未引用其他复审件。
> 基线 `89af13e`；评审对象设计稿 v2.1。migration_policy.py / 两测试文件**尚未实现**（基线仍内联 bash 正则 release.yml:379-381）。

## 1. 总评
v2.1 在口径计数与边界上真闭合了上轮 P0-2 与四条 P1，但 **P0-1 的"机械保证"为假**、**逃生门"消矛盾"论证在部署层不成立**、并**遗漏 A06 文档漂移与 rescue(6-8) 文本矛盾**——**不过门**（残留 P0 级隐患）。

## 2. 必修清单闭合核验表

| 条 | 判定 | 证据 / 是否换说法 |
|---|---|---|
| **P0-1** 判定表单一真源+两门对齐 | **部分闭合（换皮未全消）** | 架构**真**消灭双实现：判定收口 `migration_policy.py`，bash 仅 `--check` 读退出码（产出4/6）。但"机械保证"反证钉④ `grep -cE 'DROP[[:space:]]+(TABLE\|COLUMN)\|alter_column.*type_\|RENAME' <阶段4段> == 0` **不能表达零判定**：① 漏 `op.drop_table/drop_column/drop_index`、`op.rename_table`、`new_column_name`、`RENAME TO`、`ALTER COLUMN…TYPE`(SQL形)，pythonic 形态复活检测不到；② 它只测"是否含那几个字面正则"，不测"是否含判定逻辑"（任何 `grep drop` 都绕开）。⇒ 单一真源纪律在仓内无机械守护，与上轮P0-1根因同源。基线 release.yml:379-381 仍内联正则（未实现）。 |
| **P0-2** 封死 expand 携带破坏性op | **闭合（逻辑）** | `0116` upgrade 段仅 `op.drop_constraint`（:142），判定表显式排除 ⇒ 最终口径零命中，可合法 `phase=expand`。"命中⇒唯一合规=contract；expand 不得含命中"收紧自洽。反证钉⑤（删排除项→0116红）是真守护。 |
| **P1-1** 14→15 | **闭合** | 一手重扫（upgrade段，排除 SET DEFAULT/DROP CONSTRAINT）恰得 16（=15 legacy + 0122 contract），与设计名单差集空；`_LEGACY_EXPECTED=15` 与实测一致。 |
| **P1-2** allow_contract 5处 | **闭合** | release.yml:391/393/396/427/439 五处均在；产出7列全5站。 |
| **P1-3** $prev 边界 | **闭合** | `prev=$(readlink -f deploy_root/server)` 在 :365；翻链在 :444（阶段6）晚于阶段4 ⇒ $prev=上一已部署版，跨发布判断成立。首部署/同发布捆绑两行为已写明（覆盖现状"全视为新增"误判）。**同判事实更正成立**（:365 确已定义）。 |
| **P1-4** DROP CONSTRAINT 排除 | **闭合** | `0062` 与 `0116` 升级段仅 `drop_constraint`（0062:21 / 0116:142），排除后零命中⇒不进名单；产出1排除理由点名两者。 |

## 3. 致命（P0）
- **P0-RES1：反证钉④为假保证 → 单一真源纪律无机械守护**（即上 P0-1 残留）。原 P0-1 根因是"两实现可漂移"，v2.1 以一条覆盖不全的静态 grep 当守护，pythonic 形态或换字面复活检测不到、且不测"判定逻辑"⇒ 漂移风险未真正关闭，属静默复发类 P0。

## 4. 严重（P1）
- **P1-RES1（逃生门论证在部署层不成立）**：消矛盾①称"仓内 pytest 闸门=立法执行点无豁免，部署门=复检点有豁免⇒不存在 flag 绕过立法"。但 **deploy 流程从不跑 pytest**（release.yml 无 pytest/run_scenarios 调用，全仓无 CI 接 pytest），部署时唯一执行点即阶段4 bash 门——而它恰被 `allow_contract` 旁路。⇒ `allow_contract=true` 在部署层就是绕过立法执行点，矛盾未真消。须二选一：把同款 `migration_policy` 检查以"无 bypass 核心"接进部署门，或证 CI 在部署前硬跑 pytest。
- **P1-RES2（A06 文档漂移）**：`docs/architecture/A06-部署管道架构.md:268` 仍称 "`-e allow_contract=true` 是唯一的放行通道" 并按旧正则描述门；:432 仍写 "DDL 门正则不拦 RENAME（盲区）"——与 v2.1 把 RENAME 纳入判定表直接冲突。设计范围未含 A06 ⇒ 漂移；W6"不符合项:无"过称。
- **P1-RES3（rescue(6-8):631 文本矛盾未消）**：:629-632 fail 文本仍 "expand-only 前提经 DDL 门保证"。allow_contract 旁路后该前提不成立；产出7只改 :427/:439（rescue(2-5)），未触 :631 ⇒ 残留矛盾。
- **P1-RES4（留痕文件无消费方）**：`var/ddl-gate-bypassed-<release_id>.txt` 落盘后无任何读取方（pip-freeze 有 latest 滚动消费，本文件无）。自称"可审计"却无消费者=装饰，与 v2 自批的"只打印告警=装饰"同病；真实轨迹仅 flow/待办.md 手工记录。
- **P1-RES5（shared/venv 在阶段4 或末就绪）**：shared/venv 由阶段5 pip install 建，阶段4 先于它；设计靠 `command -v python3` 兜底但未验证 prod 真有 python3；两者皆无⇒硬红→所有含新迁移的发布自 DOS。须实测 prod python3 在位。

## 5. 一般 / 陷阱核对
- 反证钉⑥（缺 contract_reason→preflight assert 红）：当前 release.yml preflight 区无 contract_reason/assert（grep 证实），属待实现，逻辑可行。
- `data_platform/__init__.py` 已核实存在 ⇒ pytest import 无忧。
- `$prev` 语义已核实自洽（翻链晚于阶段4），非陷阱。
- 留痕 `copy` 须保证 `release_id`+时间戳模板（ansible_date_time）可取。

## 6. 全新红队发现（独立于上轮）
- R1 = P1-RES1（部署链根本不跑 pytest）——上轮未指出。
- R2 = P1-RES2（A06 漂移）——上轮未提 A06。
- R3 = P1-RES3（:631 文本）——上轮 §四.3 点过但 v2.1 未修。
- R4 = P1-RES4（留痕无消费）；R5 = P1-RES5（venv 时机）。
- 静默漏/上游拖垮：阶段4 若 `$PY` 解析失败且 allow_contract 开 ⇒ 门被 `when` 跳过（本就允许）；若关且 `$PY` 缺失 ⇒ 硬红（合理）。

## 7. 对"逃生门保留 + 消矛盾"结论表
- 用户裁定保留：**接受**（不属技术门）。
- 消矛盾①（两门旁路面不同）：**不同意其论证**——pytest 不在部署链，部署时唯一执行点即被旁路的阶段4门，"flag 绕过立法"在部署层仍成立。
- 消矛盾②（须带 contract_reason）：同意，机械可行。
- 消矛盾③（留痕落 var/）：**部分同意**——落点可写，但无消费方，审计宣称落空。
- 消矛盾④（复活条件可判定）：同意（离开调试期+待办记录）。
- 消矛盾⑤（批84 (iii) 同步）：同意方向，但 A06 同步遗漏（P1-RES2）。
- **结论：有条件同意**——保留可接受，但"矛盾已消"不成立（残留 P1-RES1/2/3）。须补：CI/部署硬接 pytest，或承认部署门即立法本体的旁路；修 A06；修 :631。

## 8. 过门判定
**不过门**。硬理由：存在 P0 级残留（P0-RES1 反证钉④为假保证，单一真源纪律无机械守护，与上轮 P0-1 根因同源）；且上轮 P0-2 虽逻辑闭合，但"消矛盾"在部署层不成立（P1-RES1/2/3）。P1（RES1–RES5）须给处置结论。依过门判据"无 P0 即可过门"——现有一项 P0 级残留 ⇒ **不过门**。
