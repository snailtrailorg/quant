# 紧急处置账号校验 · v4 双盲独立审查（专家 B）

结论：**2 项 P0、2 项 P1**；v4 把"下发判活 vs 展示健康度"分离对了，但新裁定的"重发"半边不可实施且幂等源选错，会上演重复平仓，不可开工。

## P0
- **P0-1｜重发幂等源选成进程内存，重启/多实例必重复平仓**：方案 §3.4/§4① 用"worker 内存去重表（不落库，TTL 10min）+ 结果缓存"防重复，但 worker 由 systemd 拉起、crash 走 `os._exit`（`hub_worker.py:905-911 _on_fatal`）后内存表清空；重发命令重新 `set risk:cmd:{tid}` → 新 worker `GETDEL`（`hub_worker.py:63`）取到、内存表空 ⇒ 再跑一遍 `_close_all`（`:205-224`）重复平仓。现成的跨重启幂等源就在 `trading.py:376/378` 的 `risk:cmd:result:{req_id}` / `:final`（TTL 30min，由 `:565-579 write_cmd_result` 写）却被弃用。修法：worker 执行前先查这两个 Redis 键判"已处理"，命中即回写缓存结果、绝不重跑；内存表至多做进程内二次闸，不得作判定真源。
- **P0-2｜六面 DoD 通篇没有"重发"的端点/请求体/触发点，方案不可实施；现状用户再点必重复平仓**：方案 §0 自认"web 无重发逻辑必须补"，但 §4 六面只字未提重发走哪个 HTTP 端点、带什么 body、FE 哪个按钮/何时触发（§4③ 反而写"不改按钮 disabled"）；而现状每次点清仓都 `uuid4()` 新 req_id（`risk.py:361`、`Emergency.vue:267`），用户再点一条新命令经 `GETDEL` 独立执行（同 tid 的 EmergencyWorker 队列串行但 req_id 不同、去重表命中不了）⇒ 必然二次全平。修法：六面补齐重发端点签名（如按 req_id/tid 复用原 id 重发）+ 请求体 + FE 触发点，并明令 FE 再点须复用旧 req_id 而非新建，否则"幂等铁律"是空话。

## P1
- **P1-1｜skipped 数据通路断在 cmd 解析层，刷新可见承诺落空**：方案 §4③ 许诺"cmd 注入 skipped → worker `_final` 读回 → recent 可读"，但 `consume_risk_cmd` 的 item 只保留 req_id/verb/operator/operator_uid/ts/order（`hub_worker.py:79-86`），不透传 `skipped_accounts`，`_final`（`:281-311`）构造的 payload 自然也不含它；方案 §0/§4 未点名改 `:79-86`。修法：cmd 载荷与 `:79-86` 解析同增 `skipped_accounts`，`_final` 写回 `:final`，并在 §5③ 加"刷新后 recent 含 skipped"断言（现仅 grep 字符串不足以证通路）。
- **P1-2｜409 文案与新裁定矛盾**：新裁定下 409 判定已不含心跳（方案 §4② 三态改为全局 `live_running_tasks()` 为空），但 `risk.py:343` detail 仍写"live_task running ∩ 心跳新鲜为空"，方案未点名改；实施者只换判定不换文案即留错误口径。修法：同步改该 detail 为"无 status=running 的实盘任务"。

## 核验无误的关键事实（证明逐条真读）
- 下发/展示/manual-order 三口径分离**文本层面成立**：§4② 要求把下发枚举从 `_live_runners`（`risk.py:335`，内部 `:253` 心跳）换为 DB-only `live_running_tasks()`（`config_store.py:176-182`）；展示 `running` 保留心跳（`risk.py:444`）；manual-order 仍用 `_live_runners`（`risk.py:594`），方案 §0 明令不得破坏——只要改的是 `:335` 调用点而非 `_live_runners` 函数体，manual-order 及其测试（`test_manual_order.py:62/84`）不受影响。
- 三态分叉逻辑自洽：删 `:326-334` 预校验后，`live_running_tasks()` 全量判 409、`∩accounts` 空而全局有 running 判 400（`risk.py:336-343`），`test_batch171o.py:75-80` 的 400 断言仍过。
- §5 验收全只读 GET/grep/pytest mock，明禁对 prod 下发 close-all，"全量绿不算证据"立场正确；行号引用（`risk.py:253/444/361`、`hub_worker.py:63/79-86/281-311`、`trading.py:376/378`、`config_store.py:176-182`、三语 `1180/2645/4416`、`api.js:30-39`）实测准确。
- GETDEL 天然单消费，首次下发本身不重复；重复只在"重发 + 新/重启 worker"路径——这正是 P0-1/P0-2 的根。
