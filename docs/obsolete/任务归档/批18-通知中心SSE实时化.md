# 批18 · 通知中心 SSE 实时化（Valkey 跨进程桥）

> 2026-09-13 用户裁定（候选②先做）。批14 建的 SSE 通道目前唯一消费者=扫码向导；通知铃铛仍在
> 60s 轮询。本批接通知事件 → 铃铛/通知抽屉近实时刷新，轮询降级为兜底。
> **前置事实（v2 修正）**：notify() 调用方=**全部服务进程**（web-api 线程池/celery 双 worker/
> health_monitor/strategy_runner 实盘子进程，grep 17 文件实证）——SSE 端点仅在 web-api 进程。
> 人人 publish、单点收口（仅 web-api 跑桥回填本地 bus）、进程内自环绕桥亦是预期路径（**不做
> bus.publish 双写**——eventbus:107 旧 docstring 的「接入时改双写」随本批废弃，防同进程双帧）。

## 18A 后端 Valkey 桥

1. **`quant_common/eventbus.py`**：
   - `publish_cross_process(uid, ev_type, data)` 实现：uid=0 → `PUBLISH quant:sse:all`，
     否则 `quant:sse:u:{uid}`；payload=`json {"type": ev_type, **data}`。
     **redis 顶层导入**（层测试白名单加 redis，与 cryptography/dotenv 同审批路径——惰性导入=
     绕过层测试的隐性化）+ 全 try/except 永不 raise；**每次 publish 新建短连接**
     （celery prefork 下模块级池 fork 不安全）且 **socket_connect_timeout=1/socket_timeout=1**
     （盲审A-P0-2：缺省无超时在 Valkey hung 时永久阻塞，notify 挂在实盘告警路径=冻结交易主流程）。
   - `bus.publish_all(ev_type, data)` 新增：`_dispatch_all({"type": ...})`（现仅哨兵用；
     广播给全部本地 watcher）。
2. **桥主体落 `quant_common/sse_bridge.py`**（盲审B-P2-1：可测性——消息解析+循环独立于装配），
   **main.py 用既有 `@app.on_event("startup"/"shutdown")` 装配**（盲审B-P1-0：main.py 无 lifespan，
   新增 lifespan 会让既有 on_event 五段钩子静默失效！）——startup 起桥 daemon 线程；
   shutdown **先停桥再 bus.close()**（盲审A-P2-4）。桥循环：`psubscribe('quant:sse:*')` → listen；
   频道后缀 `all`→publish_all / `u:{uid}`→publish；**单条消息 try/except 隔离（毒消息不断流）**；
   **重连前再查 stop 事件**（防关停后 5s 幽灵重连）；断开 5s 退避。仅 web-api 进程跑桥。

## 18B 通知生产者

3. **`alert_notify/notify.py`**：PG insert 成功（notif_id 非空）后
   `publish_cross_process(0, 'notification', {})`（**payload 只带 type——盲审A-P0-1：title/
   category 明文广播给全部连接=破可见性矩阵**，email/system 类仅 admin 可见；前端本就只触发
   重拉，服务端过滤零变化）。顺手修 `_redis()` 同病（双 1s 超时）。**去重命中（return None）
   不广播**——与铃铛数据一致性对齐。

## 18C 前端消费

4. **MainLayout**：sse 单例 subscribe（引用计数既有契约）；handler 收
   `data.type==='notification'` → **400ms trailing debounce 后 `loadNotifs()`**（盲审A-P2-2/
   B-P1-3：突发风暴 K×N 并发拉+乱序回退）；**offSse 挂 onUnmounted（与 notifTimer 同点）**
   （盲审B-P1-5：logout 走路由切换不整页刷新，悬挂订阅带死 token 重连→401 整页刷冲登录态）。
   抽屉 `@open` 顺手补一次 loadNotifs（多标签页 ack 纠偏，盲审B-P2-7）。
5. **轮询兜底保留 60s 不变**（批14 契约「保留轮询兜底」；SSE 提供即时性，轮询纠偏）。

## 18E 信任边界（盲审A-P2-3）

本机 VALKEY_URL 无密码——同机任意进程可 PUBLISH `quant:sse:*` 伪造帧。当前帧语义（触发
重拉+服务端过滤）下伪造无害，知情接受。**铁律：本频道永不承载免服务端校验的渲染数据或
操作指令**——未来帧语义变富前必须先做服务端重验（uid 通道启用时同此约束）。

## 18F 部署验证（盲审B-P1-4；B-P2-4 修订为可执行路径）

主验证（生产者→全链→前端）：**`POST /api/alerts/test`**（告警测试端点，已认证）触发真实
notify → 浏览器（已登录态）铃铛**秒级**跳数（producer→PG insert→publish_cross_process→
Valkey→桥→publish_all→SSE 透传→前端 handler→loadNotifs 七段全验）。60s 轮询兜底期间
SSE 断连时的表现=最长 60s 延迟（预期非故障）。
（原 redis-cli 造帧步骤不可执行——服务器无 redis-cli 且无 shell 通道；桥半段已由
`test_run_loop_dispatches_pmessage_and_stops` 钉住。）

## 18D 测试

- `publish_cross_process`：monkeypatch redis → 断言频道/payload/永不 raise 三态
- 桥：伪 pubsub 消息流 → 本地 bus watcher 收到（all 与 u:{uid} 两路）
- notify：patch publish_cross_process → insert 路径调用断言
- 前端 build

## 边界与不做

- 不做按人定向推送（uid 通道留给未来 IM/单用户场景）；不做 ack 广播（本人 UI 本地响应）
- 不动扫码向导消费者；不动 SSE 端点契约（hello/心跳/max-age 原样）

## 修订记录
- 代码双盲审（A：P1×1+P2×6；B：P1×2+P2×6）全吸收：**action 判定改 `d.adjusted is not None` 语义**（历史 bug——"截断" in reason 把场内截断拒单记 adjust、真覆写记 approve）/ensure_bridge stop 后重建/频道常量收口单源/去抖定时器卸载清理/地板值前置防 slice 截尾/notify 测试隔离 dispatch/桥 run() 循环+socket>POLL 隐式契约入测/规则码静态扫描钉子（拒单分支全打码+前端地板⊆后端码集）/注释漂移三处/18F 改可执行路径。**实现偏差**：桥循环用 get_message(timeout=5) 轮询（方案原文 listen——无消息流时 stop 永不被检查，轮询使 stop 必达）。
- 2026-09-13 立项
- 2026-09-13 v2：方案双盲审 A（P0×2+P1+P2×4）+B（P1×5+P2×7）合并 16 项全吸收——payload 只带 type（可见性）/redis 双 1s 超时+短连接（防挂死实盘告警路径）/on_event 装配（非 lifespan——既有钩子不杀）/桥落 quant_common 可测/毒消息隔离/去抖+卸载语义/去重不广播/抽屉@open 补拉/信任边界+部署验证两节
