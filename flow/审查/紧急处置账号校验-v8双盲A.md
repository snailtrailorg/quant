# 紧急处置账号校验 · v8 双盲审（专家 A）

结论：v8"统一进度事务"方向可取，但退役三套旧回执的落地链断点多、且打断 manual-order 与既有测试——**3 P0 + 3 P1，禁止按现状开工**。

## P0（阻断开工 / 打断链路 / 不可实施）

- **P0-1｜退役主回执键却没改 5s 回执窗，web 同步返回无源**：方案 §3.2/§4 通篇未提 `_dispatch_risk_cmd:378-394`——它 poll `risk:cmd:result:{req_id}` 并 GET+DELETE 装配 `results/pending/no_quote`（risk.py:384-395，前端 `fmtResult` Emergency.vue:103-136 全靠这些字段）；退役后该键无人写→每次处置白等 5s、`results` 恒空、逐单结果全丢，若沿用 DELETE 还会删掉 progress 唯一真源。修法：§4② 明写 5s 窗改读 progress 且只读不删，并定义同步返回新装配契约。
- **P0-2｜退役回执键打断 manual-order（顾头不顾腚）**：manual-order 明确在 §2"不做"范围，却共用 `risk:cmd:result:{req_id}`——`_dispatch_manual_cmd`(risk.py:619/625)、`manual_order_result`(:678)、`write_cmd_result`(trading.py:565) 仍依赖它且不写 `:final`（立项设计 20261113 §D4）；§6"全部移除"若连写口删则 manual-order 回执断，若保留则 §5③"旧键无残留"断言不成立。修法：把 manual-order 显式排除出退役集，并修正 §5③ grep（现命令只正向 grep 新键，根本验不了"无残留"）。
- **P0-3｜§5 验收自相矛盾、按字面必红**：§5② 命令跑 test_batch171o/n（方案:138-140），二者大量断言 `write_cmd_result`/`risk:cmd:result:`/`results` 结构（171o:54/126/138/139、171n:235/239），而 §5③ 又断言这些键"无残留引用"；方案未给这批测试的改/删方案。修法：二选一——明列测试迁移后的新断言，或从退役集保留兼容层，禁同批自打脸。

## P1（严重遗漏 / 事实错误）

- **P1-1｜退役 recent+单查后"处置记录时间线"无真源**：方案退役 `/api/risk/close-result/recent` 与 `/{req_id}`（risk.py:512-583），但 timeline 是纯前端内存（Emergency.vue:63/138，刷新即清），现状靠 `loadRecent`(:175-180) 从 recent 重建；退役后 active 终局即移除、progress 只能按 req_id 单查而前端无 req_id 列表，§3.5"记录仍进时间线"落空、刷新后历史处置全丢。修法：补持久终局记录端点/键族供时间线重建。
- **P1-2｜熔断沿撤单"不入卡片"的事实依据错误**：沿兜底(hub_worker.py:1028-1031→trading.py:317-332) 正是为"防非 web 来源 halted"（方案 §0:22 自认），IM/自动风控 halt 时无 web req_id，其撤单完全不可见，违背"撤单都显示每单状态"裁定；且 §0:23 与 §3.3:96 对来源自相矛盾。修法：非 web halted 也生成 progress 记录，或显式声明为裁定例外。
- **P1-3｜`next_action`/`retry_due` 删三参数的 4 处调用未清点**：§3.4 删 retry_max/deadline/empty_max 改签名(trading.py:584-613)，调用点 trading.py:866/875 + test_batch171s.py:483-488（五处断言 `final:retries/deadline/no_quote`）签名改后必崩；§5②/§4⑤ 均未列该测试处置。修法：逐一改调用点并明确该测试改/删。

## 核验过的关键事实（确证逐文件读，非凭自述）

- halt(risk.py:204-210)→cancel-all、close-all(:503-506)→close-all 串联属实；三语 1180/2645/4416 准确；D12:61/105 心跳 TTL90s 属实；config_store:176-182 DB-only 与 risk.py:444 展示心跳分离属实。
- 路由守门 test_route_auth_gate.py:76-107 router-walk 全扫，新端点挂 Depends 即覆盖、删旧端点不入白名单——此项方案成立。
- `_cancel_working_orders` 4 消费者(hub_worker:201/212、trading:331/691)仅 1 处读返回字典、其余丢弃，增 items 兼容计数字段属实。
- 取消键竞态（方案 §3.4）：前端须先从 active 拿到 req_id 才能点取消，取消时 progress 可能未建立，终态由谁写未定义——属实现细节，未见阻断级缺陷，未列 P0/P1。
