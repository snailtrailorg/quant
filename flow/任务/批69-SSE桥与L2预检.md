# 批 69 · SSE 订阅桥 + L2 真连预检（批 61 挂账两件——同源清单）

> v1（2026-09-27 勘察底册详尽——全链锚点实测）。来源：批 61「明确不做（挂账）：subscribe_orders SSE 桥；L2 真连预检（worker/hub 会话探测）」。

## 一、勘察关键结论（方案输入）
1. **SSE 桥零新基建**：`publish_cross_process`（worker 任意进程可调）→ `quant:sse:*` pub/sub → SseBridge（web-api 装配）→ `/api/events` → 前端 `sse` 单例（fetch+ReadableStream，Profile.vue 先例）——**全链现成且经批 18 双盲审**。信号帧信任边界立法：频道只承载「触发重拉」零内容帧，永不承载数据。
2. **L2 真缺口**：hub「在场」三源可读（hb hash gen/subs+systemctl+租约），但 **MD `connected` 与 worker TD `connect_status` 均为进程内 @property、无 Valkey 导出**——「真连探测」最自然出口=**心跳 hash 增字段**（超集原则允许只增；test_runtime_pulse.py:85 字段锁需同步）。
3. **五必过语义敏感**：L2 现为②的降级注记+checklist 占位（trade_switch.py:133 `"l2_live_probe": "挂账"`）——**不入第六闸**（改 A04 §九立法需用户裁定，本批只把占位变真值：切换是半自动人工确认制，L2 真值=检查单展示项供人工参考）。
4. 顺手项：`routes/trading.py:39` hb 读缺省 URL db=4 与全仓 db=0 不一致（env 未设时静默空）。

## 二、69a · SSE 订阅桥（委托/成交实时化）

### 后端（两行 publish）
- `strategy_runner/main.py` on_order（批 68 挂点 :172-177）：`write_order_status` 后加 `bus.publish_cross_process(0, "order_update", {})`（**零内容帧**——信任边界；never-raise 同 guard）。
- `strategy_runner/hub_worker.py` on_trade（:198-202）：`write_trade_log` 后同款 `publish_cross_process(0, "trade_update", {})`。
- 写侧契约照 notify.py 先例：短连接+双 1s 超时、永不 raise（挂实盘回调路径）。

### 前端（Trading.vue 接线——Profile.vue 先例）
- `sse.subscribe`（order_update/trade_update 帧→节流 1s 合并触发 `load()`——重拉 /api/orders+position）；`sse.healthy()` 时轮询间隔 5s→30s（桥在=帧驱动，轮询降级兜底）；onUnmounted 退订。
- 帧到重拉目标=既有端点（零新读端点；成交列表现状由 orders 前端滤——不扩范围）。

## 三、69b · L2 真连预检（心跳增字段+检查单真值）

### 心跳导出（超集只增）
- **hub**：`_heartbeat`（md_hub/main.py:372-377）增 `connected=md_gw.connected`（@property 四网关统一面）。
- **worker**：`_heartbeat`（hub_worker.py:395-400）增 `td=int(bool(getattr(td_api, "connect_status", False)))`（XTP/EMT 同形；td_api None=stub 0）。
- **字段锁测试同步**（test_runtime_pulse.py:85 超集断言+两写点测试）。

### trade_switch 检查单真值
- `_checklist_l1`（trade_switch.py:133）：占位→`_l2_probe(conn, to_account)`——读目标账号 hub hb `connected` 字段+该账号在跑任务的 worker hb `td` 字段，返回结构 `{hub_connected: bool, td_connected: bool|null(无任务)}`。**不入 _recheck_locked 五闸**（展示项）。
- 前端 TradeSwitch.vue checklistItems 增 l2 渲染项（真值三态：绿=hub+td 连/黄=部分或无任务/null=字段未上报〔部署间隙〕）——词条走文案师。

### 顺手修
- `routes/trading.py:39` hb 缺省 URL db=4→db=0（与全仓一致；env 已设则零变化）。

## 四、限定范围
不做：notification 帧前端消费（批 28-3 退役后悬空——独立项）；trade_log 独立读端点（现状 orders 滤足够）；L2 入第六闸（A04 立法变更需用户裁定——本批后用户可在检查单看到真值再裁）；IM 外推成交通知（dispatch warn/critical 门槛+文案——另批）。

## 五、验收
1. pytest 全绿（+钉：两 publish 调用〔mock bus〕/hub hb connected 字段/worker td 字段/字段锁同步/_l2_probe 三态〔hub 连 td 连/hub 连无任务/字段缺失 null〕/trading.py:39 修）。
2. build 绿+词条（文案师）。
3. staging：SSE 端点流上出现 order_update 帧（无真单窗=模拟 publish 一帧验证全链）+检查单 l2 真值渲染；周一真单验证（帧驱动实时化+终态联动）。
4. pyflakes 零新增。

## 六、mock 方式
mock eventbus.publish_cross_process 断言调用；fake hb hash（connected/td）；fake conn 行集（_l2_probe）；Trading.vue 帧驱动不测组件（结构改动小——build+人工验收）。
