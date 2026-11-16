# 紧急处置账号校验 · 专家 A 独立审查报告

结论：方案对根因的诊断基本属实（P0-2"必现 400"不夸大），但 §4/§5 有 1 个 P0、3 个 P1，终稿不可直接开工。

## P0

- **P0｜§5 验收命令不可执行且具破坏性**：② 的 `curl -X POST /api/risk/close-all` 无 host、无 Bearer（裸 curl 必 401；通道须 `https://quant.snailtrail.cc` + token，凭据指针 `~/.config/quant/prod_admin.env`），照抄会在真实账号上下发撤单+全平；③ 的 `tests/test_batch<new>_account_scope.py` 是占位文件名、不存在。证据：`reference_api_access.md:9-12`、`risk.py:488-506`。修法：验收改用 paper/测试户 + token，close-all 用 mock 测试替代真 curl，并给真实脚本名。

## P1

- **P1｜三问消费者清单漏第 3 语言 + FE 回执渲染，裁定 A"明示未处置"落空**：§0 只列 `locales:1180/2645`，实际还有 `'zh-tw':4416` 同词；`fmtResult`（`Emergency.vue:103-136`）未消费 `skipped_accounts`，后端加了回执前端也不显示。证据：`locales/index.js:2,2896,4416`、`Emergency.vue:103-136`。修法：第 3 语言同步删除项，并把 `fmtResult` 列入消费者、增加 `skipped_accounts` 渲染。

- **P1｜"默认只勾 running"擅改 171o 用户裁定**：171o:18 明定"默认全选（进页面即全量处置就绪）"，§4③"默认全选（可处置的）"是偷换概念，超出裁定 A（忽略无任务账号 + 回执明示）授权。证据：批171o:12/18；方案 §3、§4③、§5① 自证。修法：保留全部 enabled 勾选，仅对 `!running` 灰态禁选并标"无运行任务"，或先请用户裁定。

- **P1｜skipped 原因判定输入未设计，§4②"删 DB-only/一次判定"自相矛盾**：产出"无任务 vs 心跳死"须再读 `live_running_tasks()`（DB 集）与 `_live_runners()`（全量无过滤）；且 `risk.py:337` 仍用 `live_running_tasks()` 判 400/409（僵尸场景误报"其余账号有活任务"），方案未点名改它。证据：`risk.py:328-337`、方案 §4②。修法：扩展 `_live_runners` 返回活/死标记，并把 337 行换为 `_live_runners()` 后从同一结果生成 skipped 原因。

## 核验事实（逐条真读）

- file:line 逐个核对：绝大多数准确；不准处仅 `risk.py:447`（running 实际 444 计算 / 457 落字段）、`api.js:30-38`（实为 30-39），均不改结论。
- 裁定引用属实：171n:29"判活=live_task running ∩ 心跳新鲜、僵尸不收"、171o:18/29/61"默认全选 / 无效 account_id=400 / 回执明示未处置账号"原文一致。
- P0-2"必现 400"不夸大：FE 默认勾选全部 enabled（`Emergency.vue:222`），任一 enabled 账号无 running 行即入 `_bad`（`risk.py:328-334`）→ 400 `RISK_INVALID_ACCOUNT`，与报错文案吻合；盘后全无任务为常态。
- 删 `RISK_INVALID_ACCOUNT` 不破坏 `test_invalid_account_400`（`test_batch171o.py:75-80` 仅断言 400，NO_MATCH 同 400）；`skipped_accounts` 对 `req_ids/results/_aggregate_lists`（`risk.py:279-294,393-401`）是纯增量、无结构冲突。
