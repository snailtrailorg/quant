# 盲审迁移两步走立法 — 三审·代理B（独立判断，仅报已核实结论）

## 1. 总评
方向落地可行，但 **P0-α 逃生门边界在 rc=2 被击穿**（模块执行错误被当"命中旁路"一并豁免，破坏"只能取消阻断、不能免检"），且 **A06 改正清单漏改 :428/:270-271**（行号漂移令旧复审结论落空）——两处须返工；反证钉④ 仍是非封闭枚举，须补强。

## 2. P0-α 闭合核验
- **rc=2 是否被豁免：是，被豁免（P0）。** 设计稿 `failed_when: ddl_gate.rc != 0 and not (allow_contract …)`（:162）对 rc=1（命中）与 rc=2（执行错误，:160）同待遇。allow_contract=true 时 `true and false`＝false ⇒ 任务不失败。模块内部报错（rc=2）被当作"已声明旁路"放行并留痕，门实则**未检测**——直接违反头部"只能取消阻断、不能免检"（:27）。证据：:160-162 合取式未区分 rc；删 :391 `when` 后此 failed_when 是唯一闸门。
- **修法**：`failed_when: (ddl_gate.rc == 1 and not (allow_contract|default(false)|bool)) or ddl_gate.rc == 2`——rc=2 恒失败（执行错误必须阻断），仅 rc=1 可旁路。
- **rc=3 vs rc=1 在 allow_contract 下可区分性**：旁路告警 `when: ddl_gate.rc != 0 and allow_contract`（:173）会把 rc=2 也触发，文案"门命中但被豁免"对执行错误是**误报**；留痕"命中=<是/否>"若按 rc==1 取，则 rc=2 写"否"而告警写"命中"，自相矛盾。根因同 rc=2 豁免。
- **preflight `contract_reason` 断言落点**：preflight 在 :30-160，阶段 4 在 :361 ⇒ 早于门 ✓；全仓无 `allow_contract` 默认值（grep 仅 :439 文本字面，group_vars 零命中）✓ 纯 `-e` 旋钮；断言 `when: allow_contract|default(false)|bool` 写在 preflight 语义正确（仅旁路时需 reason）。
- **诚实边界完整性**：W6 三条未穷尽。第四条＝**allow_contract=true 且模块 rc=2（门崩溃）⇒ 零检测窗口**（同 rc=2 根因）；首部署(rc=3 跳过)+allow_contract 叠加时 rc=3 与 rc=2 不同待遇但均不阻断，须显式区分。

## 3. P0-β 闭合核验
反证钉④（:229-230）：
- **⑵ 是反面枚举，非封闭正向。** 仅列 `[[:space:]]`/`\|`/`.*` 三字面；`+ ? [0-9] [^ ] \{n\} \( \) \d` 等均不在清单 → 漏测。`[[ ]]`/`[ ]`/`case`/`test` 用 bash 内建（glob、`?`、`[ ]`）不含清单字面也能做"按 rc 选行为"判定。
- ⑴ 枚举 grep/awk/sed/egrep 四类工具亦为枚举；`python -c "…"` / `python - <<EOF … EOF` 内联 python 判定：**不含 ⑴ 任何字面、不含 ⑵ 清单、不含 ⑶ "migration_policy.py" 字面** → 三钉全漏。故钉④整体**非封闭**。
- **⑶「恰 1 次」脆性**：若 bash 合法加 `test -f "$M"` 再 `"$PY" "$M" …`，字面出现 2 次 ⇒ 误红（过度约束）。建议改"仅一次命令替换式调用"。
- **判据自身守卫**：钉④ 靠人工跑 grep 验收（:227），非 pytest 机械执行 → 与 P0-β「机械守护」非同一件；应加一条 pytest 读 release.yml 段断言"除一次模块调用外无 grep/awk/sed/egrep/`[[`/`case`/内联 python"。

## 4. 新增项回归核验
- **受管集 sha256 差集**：① prev_dir 缺失/None ⇒ 返不可判定(rc=3) 与"首部署跳过"一致 ✓；② `$prev=$(readlink -f …)`（:365）解析为上一已部署版实路径，A06 §4.5:281 明确 `server` 指向项跳过 GC ⇒ 上一版必在 keep=5 内 ✓ 安全。
- **A06 四处**：grep 实得 :268/:275/:426/:432 四条属实，但**漏 :428**（"allow_contract 只被 rescue(2-5) 消费，rescue(6-8) 不看"）与 :270-271 散文（"rescue(6-8) 不消费它 → 仍然照常自动回滚"）。设计稿改 :626 让 rescue(6-8) 自动回滚 `when` 补 `auto_rollback_disabled` 合取（:178-179，且 allow_contract 仍置该标志）→ 落地后 :428/:270-271 变假。行号漂移：旧复审引 :432＝此行，今已漂至 :428。⇒ **A06 改正清单不全（P1）**，须补 :428 与 :270-271。
- **:628-635 条件化**：`{% if auto_rollback_disabled|default(false)|bool %}…{% else %}…{% endif %}` 语法可行——文件已有先例 :439、:633-634 同形态 Jinja ✓；该段确在 rescue(6-8)(:615-635) ✓。
- **产出 5① 口径**：①(非白名单命中→唯一合规=contract)/④(legacy 白名单双向) 覆盖闭合——声明行互斥（phase=expand|contract 或 legacy 三选一），无五条都不管的文件；白名单内 legacy 恒归 ④。仅建议明写"声明三选一互斥"。一般。
- **站点清单**：grep 现有 5 处 :391/:393/:396/:427/:439 仍准，无第 6 处 ✓；:391 改 failed_when（引用仍在），393/396/427/439 保留改文案，新增 preflight 断言/旁路告警/留痕 3 处 ✓。

## 5. 新红队发现
- **静默漏检**：`rel` 在阶段 4 bash 内 :364 已定义 ✓；但 `$PY` 取不到致路径错 → rc=2，叠加 allow_contract 即静默放行（见 P0-α）。`evaluate(path, chain_dir)` 的 chain_dir 由 check_release 内部以 new_dir 派生（:135-136 CLI 只传 new/prev），**接口自洽** ✓。
- **scan_upgrade_section** 无 `def downgrade` 时须优雅降级（awk `/^def upgrade/,/^def downgrade/` 缺尾则扫到 EOF）；建议显式处理。一般。
- **与制度冲突**：`test_deploy_blast_radius.py` 仅扫 `deploy/wrappers`、`deploy/**`、`server/scripts/**`（:159-255），**不扫 `server/src`** ⇒ 新增 `migration_policy.py` 不会触发"同批改 wrappers" ✓ 无硬冲突。D18 §2.3 仅加一行指针 ✓。
- **文档漂移（除 A06）**：`flow/进展/2026-W40.md:35`、`flow/decisions.md:508`、`批85` 含 EXPAND-CONTRACT 描述，落地后由"设想"转"已实施" ⇒ 转真非假；收口条件:303 复核"唯一放行通道/盲区"表述——除 A06 外 grep 未见残留。
- **migration_policy.py 落地可行性**：`server/src/data_platform/__init__.py` 存在 ✓；pytest.ini 无 pythonpath，但既有测试 `from src.data_platform.db import …`（test_jsonb:62）同机制 ⇒ 可作 `from src.data_platform.migration_policy import …` 导入 ✓；纯 stdlib＋`__main__` 守卫 ⇒ 两用可行 ✓。

## 6. 需新裁的点
- P0-α rc=2 修法是否采纳（failed_when 拆 rc==1 / rc==2）。
- A06 改正清单扩到 :428/:270-271；或先裁定 rescue(6-8) 是否真吃 `auto_rollback_disabled`（若不吃，则 :178 与 :428 须同步定调，避免 release.yml 与 A06 再分裂）。

## 7. 过门判定
**不过门**。硬理由：**P0-α 逃生门边界在 rc=2 被击穿**——模块执行错误被当"命中旁路"豁免，破坏用户裁定铁律"只能取消阻断、不能免检"，属"声称的边界不可实施／静默绕过面未关死"，须返工。A06 漏改 :428 为 P1（须给处置）。余见上。
