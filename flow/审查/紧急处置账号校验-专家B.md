# 紧急处置账号校验 · 专家 B 独立审查（双盲）

结论：**1 项 P0、5 项 P1**，方案不可按现状实施。以下仅列 P0/P1。

## P0
- **P0-1 §5 验收②会真实全局熔断+平仓，且照抄不可执行**：方案:91-94 的 `POST /api/risk/close-all` 无 host/token（端点 `require_perm("halt")`，risk.py:490）、`<有任务账号>` 是非法 JSON；填入真实值即先置全局 halted（risk.py:503-505）再写 `risk:cmd:{tid}`（:362）真下单。修：改 mock/隔离环境或 staging，命令补全 host+token+变量，并明令禁止对 prod 跑 close-all。

## P1
- **P1-1 「回执明示未处置账号」没有前端消费链**：裁定 A 的核心（方案:59-63/④）是回执明示，但 §0 消费者清单（方案:14-22）漏掉 `Emergency.vue:103-136 fmtResult` 与 `:147-168 fmtFinal`，两者只遍历 results/pending/no_quote/retrying；worker 产 `:final`（hub_worker.py:290-311）也无该字段 ⇒ 字段返回但用户永远看不到。修：两函数列入清单并渲染三语词条，或由 worker 写入 `:final`。
- **P1-2 前端默认只勾 running 违背 171o 且非裁定 A 所需**：171o 归档:12/18 原文「默认全选（进页面即全量处置就绪）」、数据源=trading_account enabled 行；方案:61/71 改成 `enabled && running` 却称「保留裁定」，而裁定 A 仅含忽略+回执明示，后端已忽略后无需改默认。修：删该前端默认改动，保留默认全选+skipped 回执；确要改须先取得用户对 171o 的裁定修订。
- **P1-3 skipped 的「心跳死」原因拿不到可靠依据**：`_live_runners` 在 Valkey 不可达时整体吞异常返回空（risk.py:257-258）、单 runner hgetall 异常静默 continue（:255-256）；方案 §4② 要求区分「无任务/心跳死」，实际只会把 DB running 但被过滤者一律标心跳死，把故障误报成因（且现阈值 30s 与 D12:105 的 90s/告警未对齐）。修：让判定返回 per-runner 原因（valkey_unreachable/hb_error），或原因只写「未处置（无活 runner）」不臆断。
- **P1-4 六面 DoD 缺可观测/告警面**：方案 §4 六面=数据/写入/前端/文案/测试/文档，缺项目 DoD 的可观测面；现有 audit_log 只记 `dispatched=N acked=M`（risk.py:389-390），被跳过账号及原因无审计痕迹。修：补该面=skipped 落 audit_log+状态可见，并加行为断言。
- **P1-5 消费者清单漏第三语言块且行号不准**：locales/index.js 实为三块（zh:2 / en / `'zh-tw':2896`），`RISK_INVALID_ACCOUNT` 在 :1180/:2645/:4416；方案:21 自称「已逐个核实」却只列前两处，且把 exposure `running` 引成 risk.py:447（判定实际在 :444、字段在 :457，447 是 except）。修：补 :4416 与正确行号，否则 zh-TW 残留「刷新重试」。

**核对无误（非问题，证明已逐条打开）**：171n 判活口径（归档:29）与 171o 三处裁定（归档:17-18/29/60）引用属实；P0-2「必现 400」成立（171o:7 prod 实答 4 账号无任务态 + Emergency.vue:222 默认全选 + risk.py:328-334 `_bad`）；`skipped_accounts` 对 `req_ids/results/_aggregate_lists`（risk.py:279-294,393-401）为纯增量无冲突；删 `RISK_INVALID_ACCOUNT` 不破 `test_invalid_account_400`（test_batch171o.py:75-80 仅断言 400，NO_MATCH 同为 400）；§5③ pytest 文件为待建新测试占位符，可接受。
