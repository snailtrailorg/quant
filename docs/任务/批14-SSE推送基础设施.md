# 批14 · SSE 推送基础设施 + 首个消费者（扫码确认段）v2

> 任务基线：只读本文 + `docs/architecture/19-IM统一接入设计.md`。
> 立项裁定（2026-09-10 用户）：SSE 而非 WS——单向推送、一次建全站受益；首个消费者=扫码确认段（替代 3s 轮询）。
> **v2 = 方案双盲审 A（P1×4+P2×7）+ B（P0×2+P1×4+P2×5）全吸收**；三条双判同向（wait_for 语义/publish 隔离/nginx 不在管道）。

## 1. 目标与范围

**目标**：`/api/events` SSE 通道（认证+心跳+看门狗+优雅停机）+ 进程内事件总线（quant_common）+ 扫码确认段推送化（轮询降为兜底）。

**范围外**：通知中心（跨进程，Valkey 桥后续）/回测进度//ws/pnl/admin 面扫码（owner=None 无用户维度，轮询不动）。

## 2. 架构事实（已核实）

| 事实 | 结论 |
|---|---|
| uvicorn 单 worker；systemd `KillSignal=SIGINT`+`TimeoutStopSec=30`，无 graceful timeout 参数 | 进程内总线够用；**必须补 `--timeout-graceful-shutdown 5`**（B-P0-1：SSE 永久 in-flight，默认无限等→每次发布 30s 挂死+SIGKILL） |
| run_onboarding=web 进程 daemon 线程，状态唯一写点 `_set_session`（含端点 pending 写） | 触发点一处挂全覆盖 |
| 通知产生者=celery（跨进程） | 桥接口预留不实现 |
| EventSource 不能带 header | 前端 fetch+ReadableStream（token 不进 URL/日志） |
| deploy/ 无 nginx conf；staging/prod 探针全部 127.0.0.1:8001 直连 | **管道验证面绕过 nginx**——响应头 `X-Accel-Buffering: no` 为主保险（nginx 默认生效），**零服务器手工项**；即时性验证走生产域名一次性 curl（§6） |

## 3. 设计

### 3.1 事件总线（新 `src/quant_common/eventbus.py`，层 0——纯 stdlib，test_layering 纯度过）

```python
class EventBus:
    """进程内 pub/sub。并发契约（盲审 A-P2-6/B-P2-4）：
    - watchers 的读改写全部经 call_soon_threadsafe 收敛到 loop 内执行——publish 线程只做调度，
      不直接迭代 dict（防 changed-size-during-iteration）
    - loop 惰性锚定：首次 subscribe 时 get_running_loop()（main.py startup 是 sync def 无 loop——
      A-P2-1/B-P2-4；天然兼容 pytest 每测新 loop）
    - loop 未锚定/已关闭：publish 一律 no-op（B-P1-2——停机窗口 daemon 线程 publish 不炸本体）"""
    def subscribe(uid: int) -> asyncio.Queue      # maxsize=50，满丢最旧
    def unsubscribe(uid, q) -> None               # 断开清理
    def publish(uid: int, ev_type: str, data: dict) -> None   # 同步可调，永不 raise
    def close() -> None                            # 停机钩子：向全部 watcher 投哨兵 None
```

- 事件信封：`{"type": "onboarding", "ticket": "...", "status": ..., ...}`（字段与 onboarding-status 端点载荷一致——**一致性契约测试**锁死，B-P1-4）
- 投递 at-most-once；断线重连补偿=前端回查（§3.4(b)）
- 跨进程桥：`publish_cross_process()` no-op 预留位（Valkey pub/sub 接入时填充）
- **max-age**：连接最长 30min 服务端主动断（B-P2-5——NAT 黑洞连接挂数小时无害但主动断更干净；客户端无感重连）

### 3.2 SSE 端点（新 `src/web_api/routes/events.py`）

```
GET /api/events    require_authenticated（Header JWT——零新认证面）
```

- 首帧 `event: hello`；每 **15s** 心跳（`: ping`）；**max-age 30min** 后发 `event: bye` 主动断
- 取帧：`asyncio.wait_for(q.get(), timeout=15)`，`TimeoutError`→发 ping 续等（A-P1-1/B-P1-1 双判：禁 `asyncio.wait(queue)` ghost-getter 吞事件陷阱；Queue.get 取消安全）
- 断开检测：Starlette StreamingResponse 内建 disconnect 监听为主（A-P2-6：is_disconnected 轮询冗余不加）
- 响应头：`Cache-Control: no-cache` + `X-Accel-Buffering: no`（nginx 响应级关缓冲——主保险，A-P2-2/B-P1-3 双判）

### 3.3 触发点（首批唯一）：扫码状态

`feishu_bot/tasks.py _set_session()` 末尾：

```python
r.setex(...)                    # ① Valkey 真相源优先（B-P1-2：setex 成功后再推）
if owner_user_id is not None:
    try: bus.publish(owner_user_id, "onboarding", {**data, "ticket": session_id})
    except Exception: logger.warning(...)   # ② 全吞——推送故障绝不炸状态机
```
- **载荷剥 `qr_img`**（A-P1-3：base64 数十 KB 必跨 chunk；前端出码时已同步拿到，SSE 只推状态小帧）
- bus 对 loop 态自防御（§3.1），此处无需再判

### 3.4 前端（Profile.vue + api.js）

**api.js 增单例 SSE 管理器**（B-P2-3：引用计数+共享退避——通知中心接入时每页 N 条连接是反模式）：
```js
sseManager.subscribe(handler) -> unsubscribe   // 内部：首订阅建连，归零断开；重连指数退避 1s→30s
                                               // 401 → 停止重连（B-P2-5/A-P2-5——走既有登出路径）
```
流解析契约（A-P1-3）：`TextDecoder(stream:true)` + 累积 buffer 按 `\n\n` 切完整帧 + 残尾留存 + 单帧解析失败丢帧不断流。

**Profile.vue 扫码向导接线**（组件级生命周期——B-P2-3：mount 订阅/unmount 退订，弹窗与方式切换不动连接）：

(a) **健康=帧到达看门狗**：>45s 无任何帧（hello/ping/事件）判死→abort 重连+轮询恢复 3s（B-P0-2：fetch 假活下"连接布尔"永远误判健康）
(b) **每次连接成功立即回查一次**：`pollQr()` 直发不等 interval（B-P0-2：一并解决初始竞态/resume 首查延迟/重连补偿——resume 骨架屏不再挂 15s）
(c) **事件守卫**：`ev.ticket === qrTicket.value` 才处理（A-P1-4：旧 ticket 迟到终态防误翻）+ 弹窗已关守卫（B-P2-1）
(d) **handleOnboardingStatus 抽取边界**（B-P2-1 修正）：只装"给定 d 的处理"（expired/done/error/qr_img/stopPoll/loadIm/sessionStorage）；**BUSY existing_ticket 恢复留在 startQr catch**（不在 pollQr，抽不进去）；pollDeadline 检查与 qrPollFails 留 pollQr；SSE 路径不做 deadline→timeout 翻转
(e) **预存在死代码顺手修**（B-P2-2）：`qrPollFails = 0` 从 pollQr 首行移到 try 成功路径尾部（原"连续 2 次判死"从未生效）
(f) expired 无 SSE 事件（端点合成态——A-P2-3）：倒计时归零时主动触发一次即时回查
(g) 轮询间隔：SSE 健康 15s 兜底 / 非健康 3s（现有值）

## 4. 交付物

| 文件 | 动作 |
|---|---|
| `server/src/quant_common/eventbus.py` | 新增 总线（层 0） |
| `server/src/web_api/routes/events.py` | 新增 SSE 端点 |
| `server/src/feishu_bot/tasks.py` | 改 `_set_session` 挂 publish（契约①②） |
| `server/src/web_api/main.py` | 改 路由注册 + lifespan shutdown 时 `bus.close()`（B-P0-1） |
| `server/scripts/systemd/quant-web-api@.service` | **改 `--timeout-graceful-shutdown 5`**（B-P0-1——走 install-units 通道随管道上） |
| `web/src/api.js` | 增 sseManager 单例 |
| `web/src/views/Profile.vue` | 改 接线 (a)-(g) |
| 测试 | 新增 test_eventbus（跨线程/loop 态防御/queue 满/多 Queue/哨兵 close）+ test_events_endpoint（hello/心跳/401/载荷一致性契约）；改 onboarding 相关（publish 断言）+ E2E 三态（健康 15s/断开回 3s/看门狗） |

## 5. 风险与对策（v2 增补）

| 风险 | 对策 |
|---|---|
| **停机挂死**（B-P0-1） | graceful timeout 5s + bus.close 哨兵 + systemd TimeoutStopSec=30 兜底；验收"带活 SSE 重启 ≤ 常规时长" |
| **resume/竞态回归**（B-P0-2） | 契约 (a)(b)——看门狗+连接即回查 |
| publish 炸状态机 | 契约①②（setex 后+全吞+loop 态自防御） |
| nginx 缓冲 | X-Accel-Buffering 响应头（默认生效）+生产域名 curl 一次性验证 |
| 多 worker 未来化 | 总线静默失效→轮询兜底在；Valkey 桥接口预留 |
| 死连接内存 | max-age 30min 主动断 |

## 6. 验收

1. SSE 连接建立即收 hello；15s 心跳；30min max-age bye+自动重连
2. 自助扫码：手机确认→网页**即时**翻转（<0.5s）；resume 会话首查不劣于现状（连接即回查）；SSE 断开 3s 兜底不断链；看门狗 45s 判死可恢复
3. **生产域名即时性**：`curl -N -H "Authorization: …" https://…/api/events` 观察 hello/心跳到达（绕开直连盲区）
4. **带活 SSE 连接重启 web-api**：≤ 常规发布时长（无 30s 挂死）
5. 断开后 watchers 归零；同 uid 双标签页均收
6. pytest 全绿 + smoke 全绿（/api/events 流式端点不入 smoke）
