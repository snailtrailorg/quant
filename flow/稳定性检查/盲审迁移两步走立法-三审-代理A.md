# 三审（短复审）· 代理 A · 批 111 迁移两步走立法与门

> 基线 `4748b2b`（v2.2 已推送）。独立复核，结论来自一手取数，非复述审件。未读任何「三审-代理B」。

## 1. 总评
**不过门**——v2.2 把 P0-α 的「门恒跑」与 P0-β 的「封闭正向判据」写进了设计，但二者落点都有洞：**`failed_when` 合取式把 rc=2 一并豁免**（逃生门越界免检），**反证钉④ 仍是字面枚举＋人工跑**（P0-β 要治的病原封未动）。两 P0 均未真正闭合。

## 2. P0-α 闭合核验（门恒跑＋逃生门只取消阻断）
**判定：未自洽。** 核心证据在设计的退出码映射与 `failed_when` 不自洽：
- 退出码约定（`产出6`）：模块 `2`=用法或内部错 → bash 以 `rc=2` 退出；`3`=不可判定 → bash 以 `rc=0` 退出。⇒ 落到 `ddl_gate.rc` 的只有 `{0,1,2}`。
- `failed_when: ddl_gate.rc != 0 and not (allow_contract | default(false) | bool)`。
- **rc=2 被豁免**：`allow_contract=true` 时 `ddl_gate.rc=2`（门内部错）→ `2!=0` 真、`not allow` 假 → 合取假 → **任务不 fail**。而 rc=2 是「门崩溃、根本没判定」，与 rc=1「命中」性质不同。设计自陈「逃生门只能取消阻断、不能免检」，但合取式对二者同待遇 ⇒ **执行错误被当成「干净旁路」吞掉**。
- **旁路告警误报**：`when: ddl_gate.rc != 0 and allow_contract`（`产出6` 旁路告警）会把 rc=2 也报成「命中但豁免阻断」，留痕文本 `命中=<是/否>` 在崩溃时只能填「否」⇒ 记录谎称「门已检测、仅未阻断」，实为门崩。证据：`release.yml:391`（现 `when` 即被删处）、设计 `产出6` 退出码表 + `failed_when` 段。
- **preflight 落点**：preflight 在 `pre_tasks`（`:30` 起），早于阶段 4（`:361`），且无 `when` 门控；设计「`allow_contract=true ⇒ contract_reason` 必填」断言放此处＝无条件执行。`grep allow_contract deploy/` 全仓**仅** `release.yml`（`:391/393/396/427/439`），`group_vars/`、`inventory` 无默认值 ⇒ 「无 inventory 默认」属实、合取式语义不被默认值污染。**此项闭合**。
- **诚实边界完整性（非穷尽）**：W6 三条漏列**第④条**——「首部署（rc=3→bash 0）+ `allow_contract`」叠加时，门跳过且 `ddl_gate.rc=0` 不触发旁路告警/留痕 ⇒ **声明式旁路零留痕**。设计 `产出6 边界②` 口头「须人工确认」但无记录落点。

**修法**：`failed_when` 改为 `(ddl_gate.rc == 1 and not allow_contract) or ddl_gate.rc == 2`；旁路告警 `when` 改为 `ddl_gate.rc == 1 and allow_contract`。

## 3. P0-β 闭合核验（反证钉④「封闭正向」）
**判定：钉④ ⑵ 是披着正向外衣的「反面枚举」，且整钉仍无机械守护 ⇒ P0-β 未闭合。**
- **漏测面**（钉①④全漏）：bash 可在不含 `grep|awk|sed|egrep`、不含 `[[:space:]]`/`\|`/`.*` 字面的情况下做判定——`python -c "import re;re.search('DROP TABLE',x)"`（`re` 不在清单、空格非 `[[:space:]]` 记号）、`[[ $rc == 1 ]]` / `case` / `test`（bash 内建，无清单字面）。三者都让钉④全绿却实际在判。**钉④ 不能保证「bash 零判定」**。
- **⑵ 元字符清单不全**：仅列 `[[:space:]]`/`\|`/`.*`，缺 `+ ? [0-9] [^ ] \{n\} \<` 等。即便补全，仍属枚举，会随新写法漂——正是 P0-β 要治的病。
- **⑶「恰 1 次」脆性**：合理写法 `test -f "$M" && "$PY" "$M" --check-release …` 让 `migration_policy.py` 字面出现 2 次 ⇒ 误红。应判「调用恰 1 次」而非「字面恰 1 次」。
- **判据自身守卫 = 人工**：`验收标准` 七项反证钉为人工 grep；本批新增 `test_migration_policy.py` / `test_migration_expand_contract.py` 只测模块，**无 pytest 读 `release.yml`** 断言钉④。⇒ P0-β 根因（无机械守护，会静默漂移）原封未动，仅换了人工检查的内容。

## 4. 新增项回归核验
- **受管集 sha256 差集**：`managed_set(prev_dir 缺失/None ⇒ 不可判定 rc=3)` 与「首部署跳过门」一致（`产出6` 边界①：rc=3→bash 0）。`$prev=readlink -f`（硬路径，非软链），GC `:657`「跳过 server 指向项」⇒ 上一版永不被 GC，比对基准恒在。**闭合、无洞。**
- **A06 四处**：一手 `grep -n` 确认 `:268`「唯一放行通道」、`:275`「RENAME 盲区」、`:426`「0116 过不了待裁定」、`:432`「RENAME 盲区」四条均实存，**设计引文属实**。但**不止四处**：`:270`「用 allow_contract 跑…rescue(6-8) 不消费→照常自动回滚」、`:453`「否则 allow_contract 走人工 runbook」、`:428` 表 row3「allow_contract 只被 rescue(2-5) 消费」——v2.2 使 `allow_contract→auto_rollback_disabled`（`:396`）⇒ 这些表述落地后**同时变假**。设计「四处改正」漏列 ≥3 处 ⇒ 落地即留假事实。**P1。**
- **`:628-635` 条件化**：现 `:628`(name)/`:631`(msg) 单行 fail 文本含无条件「expand-only 前提经 DDL 门保证」，`grep -n expand-only` 命中 `:628/631`，行号漂至 ~633，`release.yml` 既有 `{% if %}`（`:438-439`）证明 Jinja 可行。**但条件挂在 `auto_rollback_disabled` 而非 `allow_contract`**：`auto_rollback_disabled` 可独立设（不经逃生门），此时文本会谎称「被 allow_contract 豁免」。**P1。**
- **产出 5① 口径**：「非白名单文件命中 ⇒ 唯一合规＝contract；白名单 legacy 归 ④」——五条闸门（①非白名单命中/②pair 真实/③孤儿/④legacy 双向/⑤真源非空）互相覆盖：白名单内 legacy 由 ④ 管、非白名单由 ① 管、无文件五条都不管的空洞＝「不在受管集（无内容变更）的迁移」本就不进判定（合理）。**覆盖闭合。**
- **`allow_contract` 站点清单**：`grep -n allow_contract deploy/playbooks/release.yml` = `:391/393/396/427/439`（恰 5 处），无第 6 处；新增 3 处（preflight 断言/旁路告警/旁路留痕）设计已列。**站点清单准确。**

## 5. 新红队发现
- **`evaluate(path, chain_dir)` 接口自洽存疑**：CLI `--check-release <new_dir> [<prev_dir>]` 只传两目录，而函数签名含 `chain_dir`（pair 解析用）。合理归约＝`chain_dir=new_dir`、prev 仅 `managed_set` 用，但设计未写明 ⇒ 落地需作者猜，属欠约束风险。**P1。**
- **`scan_upgrade_section` 无 `def downgrade`**：awk `/^def upgrade/,/^def downgrade/` 无闭界时扫至 EOF，不崩但会误扫（无害）。一般。
- **`migration_policy.py` 落地可行性 ✅**：`server/src/data_platform/__init__.py` 存在（`ls` 已证）；既有 `test_jsonb_columns_guarded.py:62` 用 `from src.data_platform.db import …` 经 `cd server && pytest`（cwd 在 path）成功 ⇒ 新模块 `from src.data_platform.migration_policy import …` 同路可导入；纯 stdlib 满足「两用」。无阻碍。
- **blast-radius 不冲突** ✅：`test_deploy_blast_radius.py` 只扫 `deploy/wrappers/` SQL↔schema、哨兵仅限 `deploy/wrappers/`（`grep` 已证 `server/src` 不在其扫描面）；新增 `server/src/data_platform/migration_policy.py` 不触发同批改 wrappers。设计「不碰它」安全。
- **文档漂移另有**：`flow/任务/批84-…md:6/:82` 仍写 `.ddl-gate-bypassed`（release 树路径，会被 GC）；设计 P1-5 已列，但落地前该文件仍裸奔假事实。一般（已纳入范围）。

## 6. 需新裁的点
- rc=2 是否应被逃生门豁免（我判**否**，须改 `failed_when`）；反证钉④ 是否接受「人工 grep」保级（我判**否**，须有 pytest 读 `release.yml`）；A06 改正是否扩到 ≥7 处。

## 7. 过门判定
**不过门。** 硬理由：两 P0 均未闭合——① P0-α `failed_when` 合取式把 rc=2（门崩溃）与 rc=1（命中）同待遇豁免，逃生门越界「免检」，且旁路告警误报「门已检测」；② P0-β 反证钉④ 仍是字面枚举（漏 `python -c`/`[[ ]]`/`case`）+ 人工执行，机械守护根因未除。P1 须处置：A06 漏改 ≥3 处、rescue(6-8) 条件挂错变量、钉③脆性、首部署+逃生门零留痕。
