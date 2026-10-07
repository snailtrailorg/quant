# 盲审迁移两步走立法 · 四审（窄复审）· 代理 B

## 1. 总评
v2.3 两 P0 均未发现「设计不可实施／静默数据错误／硬冲突」级别的未闭合；P0-α 已实证闭合并经一手 ansible 验证，P0-β 意图闭合但机械执行者留三处 P1 级绕过缺口——**无 P0，判过门**（P1 须加固）。

## 2. P0-α 闭合核验（`failed_when` 闭集 + bash 纯转发）
一手 `deploy/.venv/bin/ansible-playbook` 实证（localhost）：
- p1 脚本无显式 `exit`、末命令 `(exit 2)` → `p1.rc=2`：**shell rc＝最后一条命令退出码成立**。
- p2 `set -u` 下 `prev=$(readlink -f <不存在> || true)` 得空，`${prev:+…}` 展开为空、脚本不报错 → `p2.rc=3`：**`set -u` 对空 `prev` 不触发 nounset（`${x:+y}` 本就 nounset 安全）；`readlink` 失败 `|| true` 后 prev 为空，模块按「只传 new_dir」返 3，契约成立**。
- p3 `rc=3` → `failed_when_result=false`（不 fail）；p4 `rc=2` → `failed_when_result=true`（fail）：**闭集外码恒红**。

`failed_when` 仅引用 `ddl_gate.rc` 与 `allow_contract`（均 ansible 变量，**未引用 deploy_root** → 任务中「deploy_root 可用性」问题不成立）；`register` 在同 task 运行后填充、`failed_when` 随后求值，语义正确。`>` 折叠后 `not (…)` 括号配对、`and` 优先于 `or`，表达式合法。

| rc | 含义 | fail? |
|---|---|---|
| 0 | 放行 | 否 |
| 3 | 首部署跳过 | 否 |
| 1 & allow=true | 命中豁免阻断 | 否（告警） |
| 1 & allow=false | 命中阻断 | **是** |
| 2 / 127 / 126 / 137 | 用法错/解释器缺失/不可执行/OOM | **是**（恒红） |

**判定：P0-α 闭合（实证）。**

## 3. P0-β 闭合核验（allow-list + 机械执行者）
- **逃逸面是否堵住：❌ 未堵。** 设计稿 产出5⑧ 令 pytest「抽**阶段 4** 的 shell 段」（按 `name` 匹配）。但 `release.yml` 实为：play 含 `pre_tasks:`（:25）、`post_tasks:`（:644）、两个 `block:`（:264 阶段2-5、:442 阶段6-8）；阶段4 仅是「阶段2-5」block 内一个顶层 task。把 DDL 判定逻辑挪到 `pre_tasks`／`post_tasks`／另一个命名 task／另一个 play，按名抽取的断言**完全测不到**。设计稿未堵此路。
- **⑵ 关键字禁用够吗：基本够，但兜底在白名单。** `source`/`.`/`eval`/`xargs`/`find -exec`/`bash -c`/`sh -c` 命令 token 均不在 `{set,readlink,python}` → 红；但这些靠白名单而非关键字禁用拦下。缺口：`python -c "…"` 命令 token＝`python`（白名单内），可承载任意逻辑 → **绕过**；`select` 未列入禁列（非交互下基本死代码，低风险）。
- **⑶ 模块调用恰 1 次：定义太弱。** 若按「`python` token 计数」，则把合法调用换成 `python -c "…"` 仍计 1 → 绕过。须改为「`migration_policy.py` + `--check-release` 子串恰 1 次」。
- **⑷ `failed_when` 三支断言：太弱。** 只查字面含 `rc==0/3/1`；`rc==0 or rc==3 or rc==1`（丢掉 `and allow`＝回到「命中全放行」旧 bug）仍含三字面 → 漏检。须断言 `rc == 1 and` 结构或解析求值。
- **「命令 token」分词：源文本含 `{{ }}` 未渲染** → 白名单比对的是源 token，与设计意图（bash 纯转发无判定）一致，可行。

**判定：P0-β 意图闭合（闭集 allow-list + 执行者真实存在），但机械执行者三处 P1 缺口（段作用域逃逸／`python -c`／`failed_when` 字面弱判）须在编码时加固；不升 P0 因门在其目标位置真实生效、非不可实施。**

## 4. v2.3 新增项回归
- **A06 七处**：一手 `grep -n` 复核 `:268`（唯一放行通道）／`:270-271`（rescue(6-8)不消费）／`:273`（撕裂态）／`:275`（RENAME盲区）／`:428`（0116待裁定）／`:430`（只被rescue(2-5)消费）／`:432`（RENAME盲区）**七处均准、恰好七处**；`:266` 无命中，`:320-322` 的「撕裂态」属 0115/0116 双态时序语境、非 allow_contract 门叙事，**非第八处**；`:262-263` 概要表（rescue 默认行为）仍真、无需改。结论：七处穷尽。
- **`:628-635`（P1-c）**：现状 fail 文本确在 :628-635（:631 为无条件前半句）。设计「门豁免看 `allow_contract`(且 rc==1)、回滚看 `auto_rollback_disabled`」两变量分治正确；`ddl_gate` 为 block1 注册的 host fact，在 rescue(6-8)（block2）作用域可见，`is defined` 成立；`{% %}`/`{{ }}` 混写合法。**残留（一般）**：else 分支对 `rc==3`（首部署跳过）/ `rc==2/127`（门错）也输出「门恒跑保证」，措辞在这两个码下为假——建议 else 再按 `ddl_gate.rc` 细分。
- **W6 四条**：四条（pytest 不在部署路径／首部署无覆盖／contract_reason honorsystem／首部署+逃生门双豁免）诚实且第4条为新增；略欠：第5条「`var/ddl-gate-bypassed` 无自动消费方」已在头部诚实边界点明，未入 W6 不符项——属边角，不误事。
- **S7 七例**：①~⑤、⑦ 均可由沙箱 fixture/缺 `$prev` 制造；**⑥ 门不可用恒红**可行——令 `shared/venv/bin/python` 缺失→rc=127，或模块收到非法参→rc=2，闭集兜红（与 make_sandbox_root.sh 真实建 venv 不冲突，缺 venv 即触发 rc=127）。全部可实施。
- **产出4 接口自洽**：`evaluate(path, chain_dir)` 的 `chain_dir`＝`check_release` 传入的 `prev_dir`（上一已部署版，用于校验 pair 真实存在）；`managed_set(new_dir, prev_dir)` 的 prev 缺失→「不可判定」→rc=3；CLI `--check-release <new> [<prev>]` 与两函数输入闭合一致。✓

## 5. 新红队发现
- **模块改名/移动**：playbook 硬编码 `migration_policy.py` 路径；若改名→`python 不存在.py` 退出码 2→闭集红（响亮失败，非静默）；且 pytest 的「migration_policy.py 子串」断言→0→红。二者均能捕获。移动同包并同步改路径时测试需同步更新（改名属协同改动，可接受）。
- **`rel` 在阶段4前已定义**：阶段4 段内自赋 `rel='{{ deploy_root }}/releases/{{ release_id }}'`，不依赖前序 task。✓
- **与 `test_deploy_blast_radius.py` 冲突**：该闸门扫 `deploy/**`+`server/scripts/**`（退役字面量、哨兵限 `deploy/wrappers/`、schema_expectations 真源），**不扫 `server/src/*.py`**；新模块/新 pytest 均不触其断言。✓ 无冲突。
- **沙箱 `shared/venv`**：`make_sandbox_root.sh:79` 真实建 venv，S7 不缺解释器；但设计稿 mock 注记（:317）写「就位**或退 python3**」与 v2.3「不写 command -v 兜底」自相矛盾——`python3` 兜底并不存在（且不应存在，否则 rc=127 诚实性受损）。**一般/边角**：删「或退 python3」即可。

## 6. 过门判定
**过门。** 硬理由：两 P0 均无 P0 级未闭合——P0-α 已实证闭合；P0-β 意图闭合，残留三处为 P1（段作用域逃逸、`python -c`、`failed_when` 字面弱判），不导致设计不可实施或静默数据错误。
**P1 处置结论（编码前须落地）**：
1. P0-β 逃逸面：pytest 断言扩为「整个 playbook 所有 `shell` 任务的命令 token ⊆ 白名单」或「全仓/全 playbook 恰一处 `migration_policy.py` 调用」，而不仅是「阶段4 段」。
2. P0-β ⑶：模块调用定义改为「`migration_policy.py`+`--check-release` 子串恰 1 次」，堵 `python -c`。
3. P0-β ⑷：`failed_when` 断言改查 `rc == 1 and` 结构（或解析求值），堵「丢 `and allow`」回退。
4. 一般：`:628-635` else 分支按 `ddl_gate.rc` 细分（rc=3/2/127 不输出「门恒跑保证」）；mock 注记删「或退 python3」。
