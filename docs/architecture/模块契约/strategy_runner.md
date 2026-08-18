# 模块契约 · strategy_runner（策略实盘化进程）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件。
> 配套：`接口契约.md`（Order/Position/live_task/持仓真相源）+ `模块契约/md_hub.md`（hub 侧）。

## 职责
每 live_task 一个子进程（`quant-live-task@{id}`，systemd）：建 vnpy 引擎 + XTPAdapter →
tick→bar→on_bar→信号→风控→下单；direct/hub 双模式；60s 快照/持仓真相批；SA/SB/SC 稳定性机制宿主。

## 文件结构
```
server/src/strategy_runner/
├── main.py        # 入口（--task-id 新架构 / --id 旧架构兼容）+ direct 主循环 + 共享助手
├── hub_worker.py  # hub 模式 worker（TD-only + Valkey Streams 消费，纯逻辑可测函数在模块级）
└── alert_failed.py# OnFailure 钩子（systemd quant-task-failed@ 调）
```

---

## 一、public API（稳定，可跨模块调用）

### main.py
```python
_guard(name)                    # handler 包装：异常拦截不上抛（vnpy 事件线程零保护）
_in_astock_session(now=None)    # A 股时段 931-1130/1301-1500（节假日不感知——调用方叠加"今日有 tick"条件）
session_edge(cur, was)          # 时段进入沿（staleness 基线清零专用；三处循环共用勿内联）
_flush_positions(adapter, account_id, task_id) -> None
    # ST2 持仓真相批（60s 循环取 query_position() 返回值，单事务 DELETE+INSERT+refresh 心跳）。
    # 【铁律】必须在 query_account 断线守卫内调用（O-F3：断线返回 [] 会写"新鲜空仓"假真相）；
    # 失败仅日志不阻断。direct/hub 两模式同款（hub 的 ThinTdGateway 无 vnpy init_query 常推）
build_xtp_setting()             # Broker DB 优先，.env fallback
```

### hub_worker.py（模块级纯函数，测试共用）
```python
buy_ok_check(frozen, stats, hub_alive, now, in_session=True) -> bool
    # send_order 时刻事实检查（S6 修订）：BUY 需 时段+bar<300s+hub 心跳；
    # 夜间回放（bar 流动但非时段）拒——worker 重启后 max_ts 失忆不去重（C-F2）
frozen_allows(action, frozen)   # 只判 sticky（untrusted/gap 污染事实）；SELL 恒放（R-AV2）
BarMsgState.classify(m)         # gen/seq 分类（stale_gen/gen_jump/dup/gap/ok）
```

## 二、内部关键结构（不保证稳定，改前看代码）
- `_gated_send`（C2 网关）：包 adapter.send_order——sticky 冻结拒 BUY + ctx["buy_ok"]() 下单时刻检查
- `_tick_state`/worker `stats`：`sess_*` 字段=**时段作用域基线**（沿上清零，跨日回放不污染——S6 修订）
- 停止条件：新架构查 `live_task.status`；旧架构查 `strategy_config.enabled`

## 三、依赖
vnpy（MainEngine/XtpGateway/BarGenerator）· XTPAdapter（strategy_framework）· hub_worker（hub 模式）·
data_platform.db · alert_notify · health_monitor.report_schema_findings（启动校验）

## 四、被谁调用
systemd `quant-live-task@{tid}` / `quant-strategy@{sid}`；Web `POST /api/live-task/{id}/start|stop`（经 polkit）

## 五、读写表（增量，全量见代码）
- **写**：`order_log`（WAL 时序）`trade_log`（EVENT_TRADE+重连补录，幂等 trade_ref）
  `account_snapshot`（60s，断线不写假值）`bar_shadow`（direct 影子期）
  **`position_snapshot`/`position_refresh`**（ST2 真相批，见接口契约）
- **读**：`live_task`（配置+停止条件）`bar_1min`（暖机）`strategy_config`（旧架构）

## 六、不变量
1. 下单唯一咽喉 `_gated_send`——绕过它下单=绕过全部安全门
2. vnpy 事件线程 handler 必须 `_guard` 包裹（一次异常=永久失聪）
3. 进程退出走 `os._exit(0)`（XTP 原生库 teardown ABRT）
4. 交易时段外 BUY 拒（回放防护）；SELL 永不放行限制（保止损）

## 增量（2026-08-19 模块归位）
- 五件套（guard/session/sd_notify）已迁 quant_common，本模块经别名+alert 回调注入消费（晚绑定保 patch 语义）
- build_xtp_setting 已迁 strategy_framework/broker（本模块留别名 `_build_xtp_setting`）
