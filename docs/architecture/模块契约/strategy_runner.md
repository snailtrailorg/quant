# 模块契约 · strategy_runner（策略实盘化进程）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量 + 已知差异。任务改本模块前读本文件。
> 配套：`接口契约.md`（Order/Position/live_task/持仓真相源）+ `模块契约/md_hub.md`（hub 侧）+ `模块契约/strategy_framework.md`（runtime 骨架/SDK 守卫/L2 会话契约）。
> 批 4（2026-08-27）：4a 交易域九单元单源化 trading.py；4b worker 迁 runtime 骨架。
> **批 6b（2026-09-01）：direct 退役**——hub 是唯一实盘行情模式（md_mode=direct → EX_CONFIG 拒绝），§二 改退役记录。

## 职责
每 live_task 一个子进程（`quant-live-task@{id}`，systemd）：ThinTdGateway（TD-only）+ XTPAdapter +
hub_worker 消费 `hub:bars:*` 流 → on_bar→信号→风控→下单（批 6b 起 hub 唯一模式）；60s 快照/持仓真相批；SA/SB/SC 稳定性机制宿主。

## 文件结构
```
server/src/strategy_runner/
├── main.py         # 入口（--task-id 新 / --id 旧兼容）+ hub 模式 ctx 组装（382 行；direct 主循环批 6b 删）
├── trading.py      # 交易域九单元（4a 新建，306 行）——direct 与 hub worker 单源；依赖注入零模块级可变状态
├── hub_worker.py   # hub 模式 worker（4b 迁骨架，299 行）：EngineLoop 11 钩子 + XReadSleeper 流消费
└── alert_failed.py # OnFailure 钩子（systemd quant-task-failed@ 调）
```

---

## 一、public API（稳定，可跨模块调用）

### main.py——入口与分派
```python
main()                          # --task-id（live_task 新架构）/ --id（strategy_config 旧架构兼容）
                                # 读任务→SA4 依赖探活→md_mode 校验（批 6b：direct→EX_CONFIG fail-fast，
                                # 其余一律 hub）→ _run_hub_mode
# 退出码三分类（SA4）：EX_OK=0 正常停止（不拉起，F-36 churn 根修）/ EX_TEMPFAIL=75 瞬态
#                     （systemd 重启+reconciler 接管）/ EX_CONFIG=78 永久配置错（不重启，Failed 告警人工）
_wait_for_deps(max_wait=600) -> bool     # PG 探活指数退避 5→10→20→40→60 封顶（期间喂狗防 WatchdogSec 误杀）
_run_hub_mode(sid, tid, ...)             # ThinTdGateway（TD-only 壳）+ EVENT_LOG 注册（批 6b：TD 会话日志
                                          # [gw] 可观测）+ Strategy.from_config + _gated_send 网关包装 +
                                          # ctx 组装 → hub_worker.run(ctx)
_warmup_history(symbol, n=100) -> list   # PG 暖机（worker 侧再叠流回放 _rewarm）
_guard(name) / _alert(title, body)       # quant_common.guard + safe_notify（lambda 晚绑定保 patch 语义）
```

### trading.py——交易域九单元（4a 单源化；批 6b 起 hub worker 唯一消费方）
```python
FROZEN_STALE_BAR_S = 300                      # bar 新鲜门限（buy_ok/盲视共用）
buy_ok_check(frozen, stats, hub_alive, now, in_session=True) -> bool
    # send_order 时刻事实检查：BUY 需 时段+bar<300s+hub 心跳；夜间回放拒（C-F2）；纯函数供测试
frozen_allows(action, frozen) -> bool        # 只判 sticky（untrusted/gap 污染事实）BUY 拒/SELL 放（R-AV2）
write_trade_log(d, adapter, sid, symbol)     # TradeData→trade_log（trade_ref 幂等 + RETURNING 入库观测）
snapshot_cycle(adapter, account_id, tid, baseline_cache)
    # 60s 账户快照（direct 形态：含 available_cash/单事务/SB1 断线不写假值）+ 同拍持仓批
halt_edge_cancel(adapter, halt_state, sid)   # SB2 熔断沿撤全部在场委托（F-41）；halt_state 调用方持有
recalc_hook(r, rewarm, history)              # #31 因子重算触发+热重载（rewarm 注入：direct=PG / worker=PG+流回放）
stop_due(tid, sid) -> bool                   # P4-3 停止检查（tid/sid 双态单源；节奏在调用侧）
reconcile_orders(adapter, sid, symbol=None)  # SC2 启动/重连对账：在场委托告警+成交补录+WAL 残留（runner 超集）
_flush_positions(adapter, account_id, task_id)  # ST2 持仓真相批（query_position 返回值单事务覆盖式）
_account_baseline_capital(total, cache)      # #10 基线=账户首条快照 total_value（cache 调用方持有）
```

### hub_worker.py——worker 编排与流消费
```python
run(ctx) -> None    # ctx: {tid, sid, symbol, account_id, strategy, adapter, event_engine, td_api,
                    #      history, frozen, initial_capital, warmup_pg, stop_check, reconcile}
                    # buy_ok 由 run 注入 ctx（_gated_send 消费）；frozen 与 main 网关共享同一 dict（C2）
BarMsgState.classify(m) -> str   # gen/seq 序号分类（gen 分区内 seq 连续，R-BR6/R-DL2）：
                                 # stale_gen / gen_jump / dup_or_reorder / gap / ok
_norm_ts(v) -> str               # ts 归一化（PG str() 与流 isoformat 断裂，S4）
_hub_alive(r) -> bool            # hub 心跳存在（TTL 内）；存储不可查 True（断流自然使 bar 过期）
```

#### EngineLoop 钩子表（11 项；name=live-task-{tid}，step=5.0，sleeper=XReadSleeper）

| 钩子 | period | 落位/语义 |
|---|---|---|
| xread 流消费 | sleeper 注入 | XReadSleeper（on_batch=process_batch→handle_msg→XACK） |
| stop-check | 5s | _stop_hook：stop_due→xgroup_del+os._exit(0)（正常停止码，不用 failure=exit） |
| sess-edge | 每步 | 时段沿清 sess_bar_wall 基线（S6：昨夜回放不污染今晨判定） |
| blind-watch | 每步 | 盲视判定+告警：frozen["now"] 只喂心跳/告警，下单判定在 send_order 时刻（buy_ok） |
| heartbeat | 5s | D3 定案：只写自有 7 字段+ts（{pid,md,gen,last_bar_ts,lag,bars,frozen,ts}，md 字段区分模式） |
| snapshot | 60s | trading.snapshot_cycle（快照+持仓批） |
| halt-edge | 每步 | trading.halt_edge_cancel（熔断沿撤在场单） |
| factor-recalc | 5s | trading.recalc_hook（r, _rewarm, history；含因子热重载） |
| td-reconnect | 每步 | TD 重连沿→ctx["reconcile"]()（对账含成交补录，4b 收敛冗余循环） |
| zombie-claim | 5s | xautoclaim 僵尸 pending 认领（min_idle 60s；幂等靠 ts 去重，评审 S3） |
| 看门狗+事件线程 | 每步（内建） | EngineLoop preflight（R-BR12；死→on_fatal 告警+exit） |

#### XReadSleeper 契约（实现在 strategy_framework/runtime/xsleeper.py）
- 双节奏：block ms = min(500, 距下一钩子到期剩余 ms)、钳 1ms 禁 BLOCK 0——定时钩子不可能被繁忙流饿死（5s 钩子最坏延迟=500ms+单批处理时长，与旧 worker 等值）
- never-raise：`__call__` 边界**全异常不外抛**（含 on_batch 内异常）——loop 的 sleep 位无 try/except，传穿会命中 finally `os._exit(0)`=任务静默死；Timeout 类静默返回，其他吞后睡 1s 下轮再试（禁内旋）
- NOGROUP → 直接 `os._exit(75)`（禁 sys.exit——SystemExit 会被 finally 吞成退出码 0）→ systemd 重启 → run() 启动段组重建接手（P0-3）
- 单线程模型：on_batch 在 loop 线程内联执行（frozen/history 裸 dict 无并发险）；**禁止后台线程**

## 二、direct 退役记录（批 6b，2026-09-01）
- 08-28 批 6a 切 hub（用户裁定跳门禁）→ 08-31 验证日全绿（241 根零丢失+窗开关闭环）→ 09-01 批 6b 删 direct 主体（main.py 702→382 行，MainEngine/XtpGateway/GuardedXtpMdApi/XtpMdSession/_resolve_client_id 全退）
- 误设 md_mode=direct（任务级 params 或 system_config 全局）→ EX_CONFIG fail-fast（不静默装死，盲审 B-P1）
- **知情取舍**：direct 的 live_task 退出回写（systemd stop 后 status 残 running）hub 路径无等价——L3 reconciler 按 DB 期望 300s 内拉回（手动 systemctl stop 会被撤销，停止必须走 Web，与 6a 现状同非回归）
- bar_shadow 随 direct 退役停止写入（表保留历史回溯）；三查②改 hub 单侧完整性（见 flow/待办.md 手册）

## 三、内部关键结构（不保证稳定，改前看代码）
- `_gated_send`（C2 网关，下单唯一咽喉）：sticky 冻结拒 BUY + ctx["buy_ok"] 下单时刻检查（缺失保守拒）
- worker `stats.sess_bar_wall`=时段作用域基线（沿上清零）；`BarMsgState.max_ts` 跨重启持久水位（`hub:worker:max_ts:{symbol}`，R-DL1）
- 消费组：启动 destroy + create id=$（P0-3 防旧水位重复消费）；暖机只填 history 绝不调 on_bar（F3）
- 停止条件：trading.stop_due 单源——新架构查 live_task.status；旧架构查 strategy_config.enabled

## 四、依赖
vnpy（EventEngine/XtpTdApi）· strategy_framework（adapters.XTPAdapter / broker.build_xtp_setting / runtime：loop·pulse·alerts·xsleeper）· trading（本包，4a）· quant_common（guard）· data_platform.db · alert_notify · health_monitor.report_schema_findings（启动校验）
（worker 是 TD-only：**不接** MdSessionSupervisor/MD 会话——D1 防误读注记）

## 五、被谁调用
systemd `quant-live-task@{tid}` / `quant-strategy@{sid}`；Web `POST /api/live-task/{id}/start|stop`（经 polkit）

## 六、读写表（增量，全量见代码）
- **写**：order_log（WAL 时序）· trade_log（EVENT_TRADE+对账补录，幂等 trade_ref）· account_snapshot（60s，断线不写假值）· position_snapshot/position_refresh（ST2 真相批，见接口契约）。~~bar_shadow~~（direct 影子期，批 6b 停写）~~live_task 退出回写~~（direct 专属，批 6b 随删，语义见 §二）
- **读**：live_task（配置+停止条件）· bar_1min（暖机）· strategy_config / strategy_account（旧架构）
- **Valkey**：hub:bars:{symbol}（worker 消费）· quant:hb:task:{tid}（心跳）· hub:worker:max_ts:{symbol}（水位）· factor:recalc:triggered（读+清）

## 七、不变量
1. 下单唯一咽喉 `_gated_send`——绕过它下单=绕过全部安全门
2. vnpy 事件线程 handler 必须 _guard/make_guard 包裹（一次异常=永久失聪）
3. 进程退出走 `os._exit`（XTP 原生库 teardown ABRT）：正常 0 / 瞬态与 NOGROUP 75——停止路径**不用** failure=exit（exit 1→Restart=on-failure 拉起=F-36 churn 倒退）
4. 交易时段外 BUY 拒（回放防护）；SELL 永不放行限制（保止损）
5. 快照/持仓批在 query_account 断线守卫内（O-F3：断线 [] 不写"新鲜空仓"假真相）

## 八、已知差异（知情接受——裁定全表见 docs/任务/批4-worker迁移与trading解耦.md v2.1）

**4a 双模式统一八条**（语义零漂移的唯一例外；批 6b 后 3/4 条的直接对象已退役，留档）：
1. reconcile_orders=runner 超集（在场委托+成交补录+WAL 残留）——worker 由只告警升级，启动与每次 TD 重连沿均变化
2. snapshot_cycle=direct 形态（含 available_cash、单事务）——worker 落库多一列，无消费者受扰
3. 停止检查节奏各自保持（worker 5s / direct 60s，节奏在调用侧）——统一即违反 direct 冻结
4. 熔断/重算告警文案统一 direct 版
5. write_trade_log 统一 RETURNING 版——worker 侧同步获得"成交入库"观测日志
6. 停止检查 DB 异常：worker 静默→warning 告警（返回值恒 False 不变；PG 故障期 ~12 条/min/worker 属预期）
7. reconcile 告警标签统一 `sid`（原 worker `tid or sid`）——运维 tid 反查策略需一步
8. 快照失败日志文案统一 direct 版（"写 account_snapshot 失败"）

**4b 迁骨架新增（A 卷 4b 审 P2 落档）**：
9. 步进钩子（period=0）节奏 5s→每迭代步进（≈500ms 步距）——worker 对 Valkey 的 RTT ×10（盲视 exists 每步一查等），多实例线性涨
10. 周期钩子首拍提前：注册即到期（next_due=now）——snapshot 首拍 60s→0s（启动即一拍；同批 2 hub 模式）

## 最近变更
- 2026-09-01（批 6b）：direct 退役（702→382 行）+ EVENT_LOG 注册（TD [gw] 日志可观测）+ md_mode=direct→EX_CONFIG；测试面 -4（_resolve_client_id）-3 patch 行；§二 改退役记录，§四/六/七 同步
- 2026-08-27（批 4c）：4a/4b 后 public 面全变（main 887→679 / hub_worker 437→299 / trading.py 新建 306）——本文件重写：入口分派/trading 九单元/钩子表 11 项/XReadSleeper 契约/direct 冻结语义/已知差异段（v2.1 八条+4b 两条）
- 2026-08-19 模块归位：五件套（guard/session/sd_notify）迁 quant_common（本模块经别名+alert 回调注入消费）；build_xtp_setting 迁 strategy_framework/broker（留别名 `_build_xtp_setting`）
