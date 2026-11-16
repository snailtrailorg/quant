# 紧急处置账号校验 · v4 双盲审查报告（专家 A）

结论：新裁定「running 即发、回执定结果」的判活方向正确且有现成真源，但**重发幂等机制不可实施且会直接导致重复平仓下单**。2 P0 + 3 P1，禁止按现状开工。

## P0

- **P0-1｜重发去重建在 worker 内存表上，重启/多实例即重复平仓（阻断开工+重复下单）**：方案 §3.4/§4①（方案:79-83、:89）规定「去重表内存 + TTL 10min、命中后重写原回执（结果缓存于去重表）」，而 `hub_worker.py:63` 是 GETDEL 取命令即删、全文件无任何 req_id 去重表；worker GETDEL 后执行到一半崩溃/写回执前 systemd 重启（`hub_worker.py:143-144` daemon 线程随进程消失）⇒ 内存表丢失 ⇒ web 同 req_id 重发 ⇒ worker 再 GETDEL、去重不命中 ⇒ 再跑 `_close_all_positions`（`hub_worker.py:205-224`）⇒ XTP 异步持仓未回更即重复卖单/超卖，正是 171o A-P1-4「按账号去重防双发」立法要防的后果，且「重写原回执」的源缓存在同一内存表里、重启后无源可写。修法：去重改用 Redis SETNX 标记 + 执行前读已存在的 `:final`（`trading.py:376/378/565-579` 的 TTL 30min 持久键本就是现成幂等源，方案却另造内存表），测试⑤须模拟 worker 重启/新实例后同 req_id 重发不重复执行（否则单进程单测必然假绿）。

- **P0-2｜重发的写入契约/触发点完全没设计，方案不可实施**：方案 §0 自认「web 无重发逻辑必须补」（方案:39），但 §4 六面未给出重发端点、请求体如何带回原 req_id、配置表名，也没写清是在 `_dispatch_risk_cmd` 的 5s 轮询窗内自动重发还是 FE 用户再点按钮；现状 `_dispatch_risk_cmd:361` 每次调用必 `uuid4()` 生成新 req_id、`Emergency.vue:267` 每次点清仓都走新 req_id，若「重发」=用户再点按钮则新 req_id 令按 req_id 去重完全失效、必重复平仓；且命令已被 GETDEL 删除、主回执在 web 5s 窗后还会 `r.delete`（`risk.py:387`），要重发只能重 SET `risk:cmd:{tid}`，方案从未定义谁/何时/带什么体去 SET。修法：明确一个带原 req_ids 映射的重发端点（服务端按原 req_id 重 SET）或规定 `_dispatch_risk_cmd` 内自动重试循环，并据此补齐 §4 六面与测试⑤。

## P1

- **P1-1｜下发判活替换没钉到真正的下发行，新裁定可能根本没落地**：`risk.py:335` 的 `runners = _live_runners(accounts=accounts)` 仍走 30s 心跳（`:253`），而方案 §4②（方案:90）只写「删 :326-334 与 :337；下发枚举改用 live_running_tasks()」未点名替换 :335，照字面只删不换则僵尸 runner 仍被心跳挡在门外。修法：§4② 明写「用 `live_running_tasks()`+account 过滤替换 :335，`:253` 心跳仅留 manual-order/展示」，并把测试②「僵尸 runner 仍下发」写为针对 :335 的守门断言。

- **P1-2｜skipped_accounts 的 worker 数据通路丢了一环，刷新可见承诺落空**：`consume_risk_cmd` 构造的 `item`（`hub_worker.py:79-86`）只含 req_id/verb/operator/operator_uid/ts/order、没有 skipped_accounts，`_final` 的 `payload = dict(result)`（`:291`）只来自执行结果，方案 §4③（方案:91）仅说「worker `_final` 读回」却未要求改 :79-86，字段在下发到 `_handle` 前就被丢弃；全仓 grep `skipped_accounts` 仅存在于方案/审查文档、代码零实现（单审复核 P1-B 的缺口在 v4 仍未闭合）。修法：§4③ 明确同时改 `consume_risk_cmd` item 构造与 `_final` 两处，并补「刷新后回执仍含 skipped」断言。

- **P1-3｜§2 P0-2 错把展示层心跳当下发判据，可能误导实现删掉应保留的展示语义**：方案 P0-2 证据列 `risk.py:253/444`（方案:59），但 `:444` 在 `risk_exposure` 函数体内只算 `items[].running`（落字段于 `:457`）供 FE 显示、从不参与下发，真正参与下发的是 `:253`（在 `_live_runners` 内）；方案 §1 自己又明说展示心跳保留，自相矛盾。修法：P0-2 证据只引 `risk.py:253`（及调用它的 :335），`:444` 归入展示面不动。

## 核验过的关键事实（证明确实逐文件读过，非凭自述）

- `config_store.py:176-182` 的 `live_running_tasks()` 确为 DB-only `status='running'`，与 `_live_runners` 的 DB∩心跳口径差异属实——新裁定有现成真源，无需新造。
- 三态分叉重述与现状等价：删 328-334 后 `accounts=[999]` 由 400 INVALID_ACCOUNT 变 400 NO_MATCH（全局仍有 running），`test_batch171o.py:75-80` 仅断言 400、不破；`test_batch171n.py:260` 二元组不变（v4 未改 `_live_runners` 结构，已规避单审 P1-A）。
- manual-order 走 `_live_runners`（`risk.py:594/600`）+ `manual:cid` nx 去重（`:604-606`），与 `_dispatch_risk_cmd` 的枚举改动无交叉，方案 §2/§6 边界声明成立。
- §5 验收全为 staging GET / mock pytest / 静态 grep / prod GET，无真实写操作；`locales:1178-1180/2643-2645/4414-4416` 三语行号准确，`api.js:38` detail 回退在位。
- `fmtResult`/`fmtFinal`（`Emergency.vue:103-168`）当前确实不消费 skipped，P1-2 的断链成立。
