# 紧急处置账号校验 v8 · 双盲独立审查（专家 B）

结论：**2 P0 + 5 P1，不可开工**。方向对，但"退役三套统一进度键"触到 manual-order 共用键，且 §5 验收与退役硬约束正面冲突；沿兜底可见性断言是事实错误。

## P0

- **P0-1｜退役 `risk:cmd:result:{req_id}` 直接打断批173手动买卖**：该键是急停与 manual-order **共用的唯一写口**——`trading.py:375/565-579 write_cmd_result`、`hub_worker.py:263`（manual-order 只写此键、明确不写 `:final`）、`risk.py:619/625`（下发回执窗 GET+DELETE）、`risk.py:678`（单查）完整依赖它，`接口契约.md:16` 与 `手动买卖接口-立项设计-20261113.md:29` 白纸黑字；方案 §0:61 称"manual-order 不得破坏"却 §6:160-161 硬约束"旧主回执全部移除、不得并存两套真源"，且 §3.2:81-82 以 `write_progress` 取代唯一写口，未给 manual-order 任何迁移（progress 的 6 个 status 无 accepted/rejected）。修法：把该键+`write_cmd_result` 明确划为 manual-order 专用、排除出退役集，或补完整迁移设计并纳入六面/验收。
- **P0-2｜§5 验收与退役硬约束自相矛盾，按字面必红/退役必假**：§5②（方案:138-140）要跑 `test_batch171o.py`/`171n.py`/`manual_order.py`，而 `test_batch171o.py:54-55/126/138`、`test_batch171n.py:233-239`、`test_manual_order.py:223` 逐一断言 `risk:cmd:result:` 回执结构与 `write_cmd_result(final=True)`；§5③（:144-147）grep 范围只含 risk.py/hub_worker.py/trading.py/Emergency.vue 四文件，**刻意不含这些测试与 api.js:84-85**，却断言"旧键无残留引用"。删键则②红，②绿则③的退役是假的。修法：二选一——明列测试迁移后的新断言（progress/逐单/终态）并把 tests+api.js 纳入③，或按 P0-1 保留 manual-order 键、把③限定为"急停侧退役"。

## P1

- **P1-1｜沿兜底"撤的正是 web 同时在撤的在场单"是事实错误，非 web halted 时撤单彻底不可见**：`feishu_client.py:401`（飞书）、`gateway.py:65`（LLM）可独立 `emergency_halt` 而不下发 cancel-all；`hub_worker.py:1028-1034` 每拍 `halt_edge_cancel`（`trading.py:317-332`）沿撤单无 req_id、不入任何键；方案 §0:23 自己写"防非 web 来源 halted"与 §3.3:96 直接打架。即便 web 路径，`risk.py:199` 先置 halted，沿钩子下一拍即撤，cancel-all 须等 1s 轮询（`hub_worker.py:1048`）投递，web 回执常显示 requested=0。用户裁定"撤单都显示每一单状态"未覆盖。修法：给沿撤单补系统级 req_id+progress 行（source=halt_edge），或明列为已知缺口另立批处理。
- **P1-2｜无限循环改造必破 test_batch171s 的 S5 守门，且 §5 根本不跑该文件**：`test_batch171s.py:478-489` 以 5 参直接调 `next_action` 断言 retries/deadline/no_quote 三终止，文件头:11 写明"S5 删三终止→红"，另有 6 处 `patch.object(T,"write_cmd_result")`（:236/293/439/500/516/527）；方案 §3.4:100 删三终止、§3.2:81 废写口，但 §4⑤:121 与 §5②:138 均无此文件、无断言重写。修法：§5② 加入 test_batch171s.py，明列 S5 断言改测"不因计数停+取消即停"，同步改 `trading.py:863-875` 调用点与 `next_action`/`retry_due` 签名，不留死参数。
- **P1-3｜撤单逐单 `untranslatable` 在统一 status 无落点，逐单状态静默丢失**：`trading.py:338-340/349/356` 撤单 items 返回 `requested|failed|untranslatable`（状态/vt_orderid 不可读而跳过），但方案 §3.2:78 统一 status 只有 `requested|failed|no_quote|rejected|skipped|pending`，无 `untranslatable`；实施时要么丢单要么塞进 failed 误导（它不是撤单失败）。修法：status 增 `untranslatable`（含三语）或明确映射 `skipped` 并保留 detail，禁止静默丢。
- **P1-4｜取消粒度 vs 下发粒度不匹配，点一次取消停不齐**：`risk.py:359-367` 一次 close-all 按 runner/account 生成**多个** req_id，方案 §3.2:86 取消键却是 `risk:cmd:cancel:{req_id}` 单 id 粒度、§3.5:107/110 卡片按 req_id 逐张带 ×；用户点一张取消，同批其余 req_id 继续平仓，与"统一处理"相悖。修法：明确按 operator+批次取消全 req_id，或卡片按操作聚合后一次取消全部，并纳入测试。
- **P1-5｜新卡片逐单状态/取消无三语词条、验收无孤儿检查**：方案 §3.2:78 定义 6 status、§3.5:107-111 定义卡片与取消按钮，但 §4③:119 仅写"三语词条"未列 key/译文；实测 locales 仅 `RISK_INVALID_ACCOUNT`（:1180/2645/4416）与 `finalReason`，无 status/取消三语；§5（:128-151）无"三语无孤儿"断言（v6 已指出，v8 仍未闭合）。修法：按 zh/en/zh-TW 列全部新 key 与译文，§5 加三语 key 集合相等断言。

## 关键事实核验（实际读过，判定成立）

- 下发判活 DB-only（`config_store.py:176-182`）与展示心跳（`risk.py:444`）分离、`_live_runners`（:231）仅 manual-order 用（:594）——只改 :335 调用点则不破坏 manual-order，成立。
- 三态分叉（409/400/下发）、`:343` 409 detail 去掉"∩心跳新鲜"、skipped 经 cmd 透传——方案 §3.1 点名到位，成立。
- 路由守门：`test_route_auth_gate.py:77-91` router-walk 全扫描，3 新端点挂 `Depends(require_perm("halt"))` 即自动覆盖、白名单:18 禁增量——方案 §4②/§5② 已含，成立（此点无 P0/P1）。
- `fmtResult`/`fmtFinal`/`loadRecent`/`startFinalPoll`（`Emergency.vue:103-208`）与 api.js:84-85 数据源就是 `results`+`:final`+recent，方案 §4③ 仅写"退役无数据源分支"未落具体行/端点取舍，前端改动面严重 underspecified（并入 P0-2 修法）。
- 行号引用整体准确（v4/v5/v6 已三轮校核）；六面 DoD 缺 manual-order 面与沿兜底面；171s 归档:8/98 的"三终止+不做无上限重试"是用户硬约束，v8 属方向反转，须走设计文档整篇重写（§6:162"加注记"违反状态文档重写制）。
