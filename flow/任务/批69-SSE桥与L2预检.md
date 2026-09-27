# 批 69 · SSE 订阅桥 + L2 真连预检（批 61 挂账两件——同源清单）

> v2（双盲 P1×2+P2×5 全处置——审者判回写即 PASS）。来源：批 61 挂账两件。
> **P1-1**：两字段统一 `int(bool(...))`（"1"/"0"——同 frozen 惯例，禁裸 bool "True"/"False"）；读侧 int()+键缺失→null；测试钉字面量。
> **P1-2**：l2 检查单=**实时重算**——get_session/详情路径对 nominated/confirmed 现算覆盖返回（不落库——提名时刻快照最老 600s 与其余四项 execute 实时复评不一致）。
> **P2**：collector hubs 块同步消费 connected（字段锁联动）；l2 附 xtp_session_window_open（窗外挂起=预期态标注 vs 窗内断连=异常）；hub 键不存在（SA4 延迟/TTL 过期）与字段缺失（部署间隙）分维度 hub_key_exists；多任务 td 聚合=all() 保守+running_tasks 数；前端节流=trailing（保终态）。

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
- `sse.subscribe`（order_update/trade_update 帧→**trailing 节流 1s** 合并触发 `load()`——保终态〔风暴尾终态回写后最终 UI 态必重拉〕）；`sse.healthy()` 时轮询间隔 5s→30s（桥在=帧驱动，轮询降级兜底）；onUnmounted 退订。
- 帧到重拉目标=既有端点（零新读端点；成交列表现状由 orders 前端滤——不扩范围）。

## 三、69b · L2 真连预检（心跳增字段+检查单真值）

### 心跳导出（超集只增）
- **hub**：`_heartbeat` 增 `connected=int(bool(md_gw.connected))`（@property 四网关统一面；**统一 int(bool) 形态**——v2 P1-1）。
- **worker**：`_heartbeat` 增 `td=int(bool(getattr(td_api, "connect_status", False)))`（XTP/EMT 同形；td_api None=stub 0；注释互指 _td_reconnect 缺省语义差异——探测导出 vs 沿判定）。
- **字段锁测试同步+collector.py hubs 块同步消费 connected**（v2 P2-1：字段锁断言 collector 源含字段名——联动必改）。
- 读侧统一 `int(hash.get(k))`、键/字段缺失→null。

### trade_switch 检查单真值
- `_l2_probe(r, to_account)`：读 hub hb（`hub_key_exists` 维度分离「键不存在〔SA4 延迟/TTL 过期〕vs 字段缺失〔部署间隙〕」）+`connected`+xtp_session_window_open（**窗外挂起=预期态标注，窗内断连=异常**——P2-2）+在跑任务 hb `td` 聚合（**all() 保守**+running_tasks 数——P2-4）。返回 `{hub_key_exists, hub_connected, in_session, td_connected, running_tasks}`。
- **实时重算**（P1-2）：`get_session`/详情路径对 nominated/confirmed 现算 l2 覆盖返回（**不落库**——与五必过 execute 实时复评同构）；提名落库值仅存档。
- **不入 _recheck_locked 五闸**（展示项——A04 立法变更需用户裁定）。
- 前端 TradeSwitch.vue checklistItems 增 l2 渲染项（四态：绿=窗内 hub+td 连/黄=窗内断连或部分/灰=窗外挂起〔预期〕/null=键或字段缺失〔两来源文案分列〕）——词条走文案师。

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
