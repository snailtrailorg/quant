# 盲审-四审-代理A（独立盲审员 A · 第四轮窄复审）

## 1. 总评
v2.3 两 P0 主干已闭合（P0-α 封闭式补集+bash 纯转发成立；P0-β 白名单+机械执行者已成形），但 P0-β 执行者范围、分词定义、`python -c` 留三处 P1 级逃逸面，A06 回归行号漂 2 行；**无 P0，判过门**（附 P1 处置）。

## 2. P0-α 闭合核验
- **rc 语义**：ansible `shell` 无显式 `exit` 时脚本退出码＝最后一条命令退出码＝`python migration_policy.py` 的 rc；`register: ddl_gate` 同 task 内先于 `failed_when` 求值，引用 `ddl_gate.rc` 合法（ansible 后求值约定）。
- **`set -u` 作用域**：`rel` 赋字面量、`prev` 由命令替换赋值→恒有定义；`${prev:+…}` 在 prev 空/未定义时均展开为**空且不报错**（`:+` 类在 nounset 下安全），非空时展开 `"$prev/..."`（同变量已定义）。`allow_contract` 不在 bash 出现，仅 `failed_when` 的 Jinja 用 `default(false)` 兜住。
- **`>` 折叠**：YAML `>` 折成单空格行，括号/花括号配对完整→Jinja 合法。
- **readlink 失败**：不存在路径→非零+空 stdout，`|| true`→`prev` 空→`${prev:+…}` 不传参→模块仅收 new_dir→设计约定 `check_release` 返 `3`；CLI `--check-release <new> [<prev>]` 可选 prev⇒None⇒3，契约闭合。
- **封闭性值域表**（不 fail 集合＝{0,3,(1且allow)}；其余恒红）：

| rc | 场景 | 结果 |
|---|---|---|
| 0 | 放行 | 不 fail |
| 3 | 首部署/不可判定 | 不 fail |
| 1+allow=true | 命中豁免 | 不 fail |
| 1+allow=false | 命中不豁免 | **fail** |
| 2 | 用法/内部错 | **fail** |
| 127 | 解释器缺失 | **fail** |
| 126 | 不可执行 | **fail** |
| 137 | OOM kill | **fail** |

⇒ v2.2 合取式把 rc=2 与 rc=1 同待遇的 P0 已消除。**P0-α 闭合**。

## 3. P0-β 闭合核验
- **抽段机械定义**：现 stage 4 在 stage 2-5 `block:` 内（release.yml:361-391，块 rescue :426）。若 pytest 用 `yaml.safe_load` 后只扫顶层 `tasks`，**找不到** stage 4→须递归遍历 `block:`；设计稿未明示遍历法，实现风险（P1）。
- **⭐ 逃逸面判定**：断言按 `name` 含"阶段 4"匹配单任务。把判定逻辑挪到**另一任务名 / pre_tasks / post_tasks / 另一 play**，或**别处二次调用 `migration_policy.py`**，本断言**测不到**。设计稿未堵此路⇒**P0-β 残留逃逸面（P1 级）**。建议：断言扩为**全 playbook 内所有 shell 任务的命令 token**，或断言"全仓 `release.yml` 内仅一处调用 `migration_policy.py`"。
- **⑵ 关键字禁用够否**：`select/read/source/./eval/xargs/find -exec/bash -c/sh -c` 均非白名单 token `{set,readlink,python}`⇒被 ⑴ 截住；唯 **`python -c "..."`** token 是 `python`（在白名单内）且不含禁关键字⇒**可内嵌判定逻辑绕过模块**。白名单过粗（只认 `python` 不认其参数），P1。建议断言 python 调用参数必须＝`migration_policy.py` 路径。
- **命令 token 定义**：源码含 `$(readlink …)`（readlink 在 `$(…)` 内）、`'{{…}}/bin/python'`（引号+Jinja 路径）。朴素 `shlex` 会把 `prev=$(readlink`、`'{{ … }}/bin/python'` 当整 token，**漏识 readlink / 误判 python**，致断言自身失真。须用 shell AST（如 bashlex），P1。
- **⑷ 三支断言**：只查文本含 `rc == 0/3/1`，可被 `rc == 1 and False` 或 `rc == 0 or rc == 3 or (rc == 0)` 局部改写绕过。建议断言归一化后等价"封闭式补集"原构，或 `rc == 1` 仅能出现在 `and (allow_contract…)` 合取内，P1。
- **结论**：P0-β **主干闭合（白名单+执行者已就位），逃逸面+分词+python -c 三处 P1 未堵**。

## 4. v2.3 新增项回归
- **A06 七处**：七条假事实**内容全中**（268/270/271/273/275/426/428/432；268,270,271,273,275,432 行号准），**无第八处**（266 为批84 结构性缺口陈述、320-322 为双态 wrapper 的"撕裂态"、453 为前瞻建议，均非假事实）。**但行号漂 2**：设计稿称 `:428`=`0116 过不了阶段4`（实 `:426`），称 `:430`=表3"allow_contract 只被 rescue(2-5)"（实 `:428`）。按稿行号改会改错行→P1。
- **`:626 / :628-635`（P1-c）**：`:626` 是自动回滚 `when: (prev_release_id…)|length>0`（正确锚点）；`:628-635` 为 rescue(6-8) fail 文本（正确）。两变量分工（门豁免看 `allow_contract`+`rc==1`；回滚看 `auto_rollback_disabled`）语义正确。`ddl_gate` 在 stage2-5 block 注册、于 stage6-8 rescue 可见（同 play 内 register 为 play 级变量）→`ddl_gate is defined` 可用；且新设计删 stage4 的 `when: not allow_contract`⇒恒跑⇒ddl_gate 恒定义，无作用域问题。`{{}}`+`{% %}` 混写合法。**闭合**。
- **W6 四条**：四条（仓内闸门不在部署链/首部署跳过/contract_reason honorsystem/首部署+逃生门双豁免）合理，第4条新增正当；穷尽性无法证伪但已足。无 P0。
- **S7 七例**：⑥"门不可用恒红"需沙箱注入 rc=2（坏参）/127（解释器路径破坏）；⑦"首部署"需 `$prev` 为空（不建 server 软链）。均可实施但依赖沙箱编排（共享 venv 须就位，否则连正向 rc=0/1/3 也红）→P1 级场景接线。
- **产出4 接口自洽**：`evaluate(path, chain_dir)` 中 `chain_dir` 应＝new_dir（versions 目录）以校验 pair 存在；但 CLI 只传 `<new> [<prev>]`，设计稿未明写 `check_release` 以 new_dir 作 chain_dir 调 evaluate⇒闭合留白（P1/general）。

## 5. 新红队发现
1. **模块改名/移动**：改名且同步改 bash 调用→门仍跑新名模块（仍单源，安全）；但断言按字面 `migration_policy.py` 会失配/误判。建议断言"python 调用参数以 `.py` 结尾且位于 data_platform/"而非写死名。
2. **`rel` 定义**：`rel` 在 stage4 shell 首行本地赋值（字面量），不依赖前置任务；`set -u` 下恒定义。无虞。
3. **与 `test_deploy_blast_radius.py` 冲突**：该测试扫 `deploy/**`+`server/scripts/**`、读 `schema_expectations.txt`（:69），**不扫 `server/src/*.py`**（:22）；新模块在 `server/src/data_platform/`⇒不触发。无冲突 ✓。
4. **`shared/venv` 沙箱就位**：阶段4 硬编码 venv 路径（设计明示不写 `command -v` 兜底）；若沙箱缺 venv→所有 S7 连 rc=127 红。S7 须保证 `shared/venv/bin/python` 存在或场景覆盖路径。P1。

## 6. 过门判定
**过门**（无 P0）。
硬理由：P0-α 封闭式 `failed_when`+bash 纯转发已消除 rc=2 越界免检（值域表闭合）；P0-β 白名单+机械执行者主干已就位。
P1 处置（须落）：① P0-β 执行者范围扩至全 playbook shell 任务 / 断言全仓仅一处调 `migration_policy.py`，堵 relocation 逃逸；② 白名单改认 python 调用参数＝模块路径，堵 `python -c`；分词用 shell AST；③ `failed_when` 断言归一化等价原构；④ A06 行号校正（426/428，非 428/430）；⑤ S7 沙箱保证 venv 就位；⑥ 明写 `check_release` 以 new_dir 作 evaluate 的 chain_dir。
