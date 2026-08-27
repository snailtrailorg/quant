# 14 · 共享行情 Hub 架构 · 设计 v2（ST7 · 2026-08-17）

> 需求门禁：`13-共享行情hub需求书.md`。v2 修订自对抗评审（盲审②，`flow/稳定性检查/盲审hub设计-代理B.md`）：修 3 致命（F1 gen 自砖/F2 分钟口径毒化/F3 回放无抓手）+ 8 严重 + 采纳 5 项简化，修订清单见 §10。
> 技术前提（已核源码）：`XtpMdApi/XtpTdApi(gateway)` 仅依赖 gateway 的 `gateway_name` + BaseGateway 事件转发，可独立构造（xtp_gateway.py:268-289/459-487）；**BaseGateway 有 7 个抽象方法须全量 stub**（gateway.py:160-260）；TdApi.connect 的 `log_level` 是 int（LOGLEVEL_VT2XTP["INFO"]=3）、MdApi 的 protocol 是 str "TCP"；tick.datetime 已挂 Asia/Shanghai tz-aware（xtp_gateway.py:312-314）；XTP tick.qty 是**当日累计成交量**（须差分）。
>
> **当前状态（2026-08-27）**：hub 已在产；主循环已迁 `strategy_framework/runtime/` 骨架（批 2，EngineLoop
> 到期驱动 + MdSessionSupervisor 收编 L2 自愈，行为值不变——差异见模块契约 md_hub.md「行为差异」节）；
> L3 调和扩面至 hub 单元（批 5，`_desired_units` 三源归一）。**阶段 0 影子期进行中**（bar_hub vs
> bar_shadow 双轨 diff，08-26 起重新计数——状态见 13 号头部注记）；切阶段 1 与 direct 退役走批 6。

## 1. 总体拓扑（v1 不变）

```
quant-md-hub@quant（单实例，纯数据面）：ThinGateway+XtpMdApi（只连 MD）+ 自建 MinuteAggregator
        │ XADD                    │ 异步批量落库
hub:bars:{symbol} (Valkey Streams)   bar_hub 表（影子期）/ bar_1min（切流后）
        │ XREADGROUP（组=task-{tid}）
quant-live-task@N（hub 模式 worker）：ThinGateway+XtpTdApi（只连 TD）+ Strategy
风控/下单/对账/熔断全在 worker（SA/SB/SC 零改动）
```

- hub 零 TD 会话；worker 零 MD 零合约表；worker 双模式（live_task.params.md_mode 覆盖 system_config 全局默认，**任务级开关**支持灰度并存，R-MIG1/S5）

## 2. hub 设计（`src/md_hub/main.py`）

### 2.1 行情接入
- `ThinGateway(BaseGateway)`：**7 个抽象方法全量 stub**（connect/close/subscribe/send_order/cancel_order/query_account/query_position → raise NotImplementedError，hub 数据面永不调用交易方法=R-HALT1 代码级保证）；事件转发 on_tick→EVENT_TICK
- `XtpMdApi(thin_gw)` + `_build_xtp_setting()` 复用（PI3）；`subscribeMarketData(symbol, 1, XTP_EXCHANGE)` 幂等重放每 60s（SA2 语义）
- handler 全 `_guard` 包装（R-BR12 hub 侧）；tick 记账 per-symbol `last_tick_ts/tick_count/volume_acc`
- 订阅真相源：DB（running 任务 symbol 集 ∪ 影子订阅表），30s diff（R-SUB1/R-BR18）

### 2.2 分钟聚合（自建 MinuteAggregator）
- **bar ts = 分钟末标注**（桶 [10:00,10:01) → ts=10:01:00），与 Tushare 分钟线口径一致（R-BR9；v1 曾写"分钟首标注"=错位 1 分钟，评审 F2 实锤）；影子期 diff 任务实证后才切 bar_1min
- finalize 时机：新分钟首 tick **或** 定时 flush（**11:30:05 与 15:00:05 双点**，评审 S2——午休末桶不等 13:00）
- **volume = 桶末累计 qty − 上桶末累计 qty**（XTP qty 当日累计语义，vnpy 参考实现 utility.py:253-258 同款差分；评审 S3）；amount 同差分
- untrusted 双门限（评审）：`tick_count == 0` 或（桶内 tick 时间跨度 < 桶时长 50% **且** tick_count < 3）——低活跃分钟不误冻结
- 真空分钟不投递（停牌/午休语义，R-BR21）

### 2.3 分发（Valkey Streams，v1 不变 + 明确）
- 每标的一流 `hub:bars:{symbol}`，`XADD MAXLEN ~ 5000`（≈20 交易日，慢消费者 3 周不读才可能被剪，评审确认现实不存在）；字段：`gen, seq, ts, pub_ts, untrusted, open/high/low/close, volume, amount, tick_count`
- seq：同 gen 内单调；**gen=Valkey 计数器 `INCR hub:gen`**（评审 F1——lease TTL 过期后 gen 无从推导的自砖问题根治；v1"lease gen+1"作废）
- 消息处理一律以 pub_ts(epoch) 做时序/新鲜度比较（服务器时区无关），ts 仅作去重键（评审陷阱 3）
- 前置依赖：**Valkey ≥7**（XAUTOCLAIM 语义）+ **实例级 noeviction**（评审简化 4：per-db 策略不存在）→ 部署 checklist

### 2.4 租约（防脑裂，R-DL4）
- `SET hub:lease {uuid} NX EX 30`；续期 **Lua CAS**（GET 比对自身 uuid 才 EXPIRE——裸 EXPIRE 会给新持有者续命，评审）
- 拿不到租约的两种情况必须区分（评审陷阱 8）：**Valkey 连接异常**（重试+告警，不退出）vs **NX 失败且 lease 存在**（真有他人 → 写 surrender 标记退出）
- gen 自 `INCR hub:gen` 永不回退；旧 hub 分区恢复后续期失败 → 停止 XADD

### 2.5 持久化/心跳/看门狗
- bar 落库：独立线程+有界队列，10s 批量 `ON CONFLICT (symbol,ts) DO UPDATE`；**影子期写 `bar_hub` 独立表**（不碰 bar_1min，评审 F2/简化 5），切流验证后改写 bar_1min（带 source='hub'）
- 心跳键 `quant:hb:md-hub`：pid/gen/订阅数/最新 tick ts/bar 计数/Tick 速率（TTL 90s）
- 看门狗四件套（评审 S6 补全）：WatchdogSec+sd_notify / 事件线程死亡检测退出 / StartLimit+OnFailure / **tick 断流 300s（交易时段+今日已收 tick）告警+退出**——hub 活着但行情死是全场静默失明，必须自杀重启
- hub 重启：重连→重订阅→seq 归零随 gen+1（**v1 的"seq 续用流内最大"作废**，评审 S1 自相矛盾项）→ 不补发历史（缺口由 worker gen 跳变暖机覆盖，见 3.3）

## 3. worker 设计（hub 模式分支）

### 3.1 TD-only 接入
```python
class ThinTdGateway(BaseGateway):   # 7 抽象方法：connect/close/query_* 转发 td_api，
    ...                             # send_order/cancel_order/subscribe 转发或 stub
td_api = XtpTdApi(thin_gw)
td_api.connect(userid, password, client_id, td_host, td_port, software_key, LOGLEVEL_VT2XTP["INFO"])
adapter = XTPAdapter(gateway=thin_gw, event_engine=ee, order_prefix=f"t{tid}:e{boot_epoch}")
```
- **order_prefix 含启动 epoch**（评审 S8：纯 t{tid} 重启后 client_order_id 复用 → 成交归属错认）
- TD 重连沿检测：**timer 轮询 `td_api.connect_status`**（vnpy 无断线事件，只有 write_log 文本；评审 R-BR11 核）→ False→True 沿触发重跑 `_startup_reconcile`（R-BR11）

### 3.2 消费循环（评审 S7 伪代码采纳）
```python
r = redis.Redis(..., socket_timeout=3)          # 防 Valkey 黑洞永久挂起
last_timer = 0
while True:
    try:
        msgs = r.xreadgroup(gname, cname, {stream: ">"}, count=10, block=500)
    except TimeoutError:
        msgs = None                              # 3s 超时=连接问题，走 timer 检查
    if msgs: process(msgs); r.xack(...)          # 逐条处理+ack
    if time.time() - last_timer >= 5:            # 5s 全量 timer（R-BR13 ≤5s 熔断达标）
        timer_tasks(); last_timer = time.time()
```
消息处理规则（每条）：
1. gen < 已见 → 丢弃+限频告警（R-DL4）
2. gen > 已见 → **先重置 seq 基线并触发流回放暖机**（gen 分区内 seq 才连续，R-BR6/S4——hub 重启缺口在此补）
3. seq 跳变（同 gen 内）→ 该标的冻结 + XREVRANGE 回放补 history；补不齐保持冻结+告警（R-DL2/A2）
4. (symbol,ts) 已处理 → 丢弃（持久 set 去重，R-DL1）
5. pub_ts 超龄（交易时段>60s）→ 丢弃+告警计数（R-DL3）
6. untrusted → 冻结该标的+告警（R-BR4）
7. 正常 → `strategy.on_bar(bar, history)` —— **SC 下单链路原样**（WAL/风控/幂等全复用）

timer_tasks（每 5s）：停止条件/live_task.status 检查 / 心跳写 Valkey（含 md=hub/gen/last_bar_ts/**lag=now−last_bar_ts**，R-OBS2）/ 熔断沿（R-BR13 ≤5s）/ hub 心跳检查（过期→冻结开仓，BUY 拒 SELL 放行）/ sd_notify / TD connect_status 轮询（R-BR11）/ **交易时段 N=300s 无新 bar → 冻结**（评审 S6 worker 侧补防线）
- worker 事件线程防护：adapter 全部回调 `_guard` 包装 + EventEngine 线程死亡检测退出（R-BR12 补全，与 direct 模式同款）
- 优雅停止：XGROUP DEL 清组（防孤儿，评审简化 3）
- 交易前置冻结 gate 在 place_order 维度：冻结期 BUY 拒绝+限频告警、SELL 放行（R-AV2/与 SB F-31 同哲学）

### 3.3 暖机（评审 F3 根治 + 简化）
**warmup 只填 history、绝不调 on_bar**（因子无内部状态、history 显式传入——`strategy.py` 全系如此；v1"回放灌 on_bar+replay 标志"整套作废）。顺序：
1. PG 隔日历史（`_warmup_history` 照旧）
2. `XREVRANGE hub:bars:{symbol} COUNT 240` 当日部分倒序取正序**仅填 history**（与 PG 拼接处按 (symbol,ts) 去重——评审 R-WARM1 挂名项）
3. `XGROUP CREATE ... $`（**先建组后回放**，顺序钉死，评审陷阱 6）
- gen 跳变触发的重暖机同路径（hub 重启缺口补齐，R-AV3/S4；聚合类因子状态清零=与 direct 重启等价，C3 目标据此降级表述）
- **replay=true 消息字段删除**（机制整体不需要，评审简化 1）

### 3.4 停止/熔断/快照
照旧（live_task.status/熔断沿撤单/account_snapshot 单写者）。R-TD2 校验**升级为全部任务**（含 direct，迁移并存期防同账户双 TD 互踢，评审挂名项）。

## 4. systemd（R-BR15，v1 不变）
hub 单元：WatchdogSec=90/StartLimit 5/300s/OnFailure/MemoryMax=1G/After=network-online。
worker 单元：+`After= + Wants= quant-md-hub@quant.service`（启动顺带拉起；禁 PartOf/BindsTo）。

## 5. 迁移（R-MIG，S5 修订）
- **任务级开关**：`live_task.params.md_mode`（direct|hub），缺省取 system_config `md_mode`
- 阶段 0 影子：hub 起着无 worker；direct runner 落 bar_shadow 表；每日 diff `bar_hub vs bar_shadow`（OHLCV+amount 逐根，ts 口径已对齐）≥5 交易日零差异（R-BR20）
- 阶段 1：1 个任务切 hub 模式与 direct 并存（MD 多连接允许）
- 阶段 2：全量切流；hub 改写 bar_1min；direct 保留 ≥1 迭代可回滚
- 全程走 deploy-server.sh（SE3 闸门覆盖 quant-md-hub@*）

## 6. 验收与注入（R-OBS3）
新增注入：hub kill -9（gen+1/worker 暖机补齐）/ 双 hub 抢租约 / 同 bar 重复 XADD / 11:30+15:00 双 flush / 断流 300s 自杀 / worker SIGSTOP（XAUTOCLAIM 认领）/ worker 盘中重启暖机等价（对照 direct 重启）/ Valkey 停 10s（socket_timeout 路径）。

## 7. 容量（R-CAP/R-BR2）
hub 500-700M；worker TD-only 150-250M（**阶段 0 先实测，>300M 触发重评估**）；1.8G≈hub+2~3 worker；4G≈hub+6~8。

## 8. 不做（继承）
tick 分发/跨机/TD 集中化/引用数据（v1）。

## 9. 需求→设计映射
R-AV1→2.5/4 | R-AV2→3.2(timer hub心跳+无bar冻结) | R-AV3→2.5/3.3(gen跳变暖机) | R-AV4→4 | R-DL1→3.2 | R-DL2→3.2(gen分区) | R-DL3→3.2 | R-DL4→2.4/3.2 | R-DL5→2.3/3.2 | R-SUB→2.1 | R-TD1→1 | R-TD2→3.4(全任务) | R-CAP1→3.1/7 | R-CAP2→4 | R-CAP3→2.5(bar_hub→bar_1min分阶段) | R-WARM1→3.3 | R-WARM2→2.5/3.3 | R-HALT→1/2.1/3.4 | R-MIG→5 | R-OBS1→2.5 | R-OBS2→3.2(lag) | R-OBS3→6 | R-BR1→前言/3.1 | R-BR2→7 | R-BR3→外部gate | R-BR4→2.2 | R-BR5→2.2(双flush) | R-BR6→2.3/3.2 | R-BR7→3.3(warmup不调on_bar) | R-BR8→2.3/3.2 | R-BR9→2.2(末标注+diff实证) | R-BR10→3.1(epoch) | R-BR11→3.1(轮询) | R-BR12→2.1/3.2(双侧) | R-BR13→3.2(5s timer) | R-BR14→3.2 | R-BR15→4 | R-BR16→3.4 | R-BR17→2.3 | R-BR18→2.1 | R-BR19→8 | R-BR20→5 | R-BR21→2.2/3.2

## 10. v2 修订清单（评审 ② 全量落地）
致命：F1 gen=INCR 计数器（§2.3/2.4）｜F2 ts 分钟末标注+影子期独立表（§2.2/2.5/5）｜F3 warmup 只填 history 砍 replay 机制（§3.3）。
严重：S1 seq 归零定案（§2.5）｜S2 11:30:05 补 flush（§2.2）｜S3 volume/amount 差分（§2.2）｜S4 gen 跳变触发重暖机（§3.2/3.3）｜S5 任务级开关（§1/5）｜S6 hub 断流自杀+worker 无 bar 冻结（§2.5/3.2）｜S7 socket_timeout+循环伪代码（§3.2）｜S8 order_prefix 含 epoch（§3.1）。
陷阱：BaseGateway 7 抽象 stub/log_level int/协议 str（§2.1/3.1/前言）｜XGROUP 先建组后回放（§3.3）｜Valkey≥7+实例级 noeviction（§2.3）｜Valkey 未就绪与 NX 失败区分（§2.4）｜续期 Lua CAS（§2.4）｜td connect_status 轮询检测重连沿（§3.1）｜R-TD2 全任务校验（§3.4）。
