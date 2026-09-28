# 模块契约 · health_monitor（健康监控，15 号设计）

> 本模块的 public API + 依赖 + 被调 + 读写表 + 不变量。任务改本模块前读本文件。
> 配套：`docs/design/D15-服务监控设计.md`（设计全貌）+ `docs/architecture/接口契约.md`。
> **最近变更（批 66b，2026-09-27 上产）**：hub 观测面 N 实例化——collector `snap["hubs"]`（SCAN per-account+legacy 裸键收编 account=0）+ CORE_UNITS 去 md-hub@quant；R4 期望集×在场集交叉（`_hub_expected_ids` DB 源）+legacy 过渡豁免+per-account streak（JSON dict 持久化）；R6 per-account（component=md-hub:{acct}）；render_prometheus account 标签。

## 职责
双层监控的内层：采集组件状态（systemd unit/Valkey 心跳族/依赖）→ 症状型规则判定 →
触发/恢复沿检测 → health_event 落库 + 飞书告警。自身写心跳供 Zabbix 外层反向监测（互监）。
原则：动作只基于事实信号；日历只做告警抑制；通知链独立于 Valkey 存活（D-F1）。

## 文件结构
```
server/src/health_monitor/
├── __init__.py
├── collector.py   # collect() 快照 + render_prometheus() + systemctl_units
└── monitor.py     # evaluate() 规则 + run_check() beat 入口 + report_schema_findings() 入口路由
```

---

## 一、public API（稳定，可跨模块调用）

### collector.py
```python
CORE_UNITS: list[str]          # 4 个常驻 unit（web-api/celery-worker/beat/risk，实例 @quant）——批 66b：
                               # md-hub 移出（账号级实例化 quant-md-hub@{account_id}，@quant 退役），
                               # hub 健康=心跳键 SCAN 动态发现（HUB_HB_PATTERN），不在 CORE_UNITS
systemctl_units(units) -> dict  # 批量 ActiveState/SubState/NRestarts；失败返回 {}（=证据缺失）
collect(now=None) -> dict      # 幂等快照 {ts, units, deps, hubs, tasks, valkey_memory, resources, ...}；
                               # 子项失败不拖垮整体。snap["hubs"]=N 实例 dict（批 66b：SCAN
                               # quant:hb:md-hub:* per-account 在场集 {account_id: {gen,subs,ticks,
                               # sess_ticks,bars,dropped_pg,tick_age}}；legacy 裸键兼容读——per-account
                               # 集空时收编为 account_id=0，键切换/回滚窗观测面不瞬盲，终态应消失）
                               # snap["resources"] 各 kind 按周期采集（_cached_kind 进程内节流）：
                               # mem/swap/disk={total,used,pct[,paths]}；cpu={pct}（批 78——无字节
                               # 语义，消费方须分支）；snap["thresholds"] 与判定同源（含 cpu 档）
render_prometheus(snap) -> str # Prometheus 文本（按指标族分组——严格解析器兼容）；hub 指标族带
                               # account 标签（批 66b：quant_hub_*{account="{acct}"}，legacy 收编
                               # account="0"；无实例时 quant_hub_hb_present 0 无标签）；
                               # 批 78：resources 循环对 cpu 分支只发 quant_res_cpu_pct 单族
                               # （used/total 字节两族不发——cpu dict 无此键）
```

### monitor.py
```python
_hub_expected_ids() -> list[str]
    # 批 66b：R4 期望集 DB 源——external_interface enabled 且 'trading'=ANY(capabilities) 且
    # provider∈list_md_gateway_providers() 白名单（同 SA4/deploy，防未实现行误报缺失）；
    # 低频 30s 查询不入 collect（/metrics 高频）；查询失败返回 []（evaluate 降级空集地板判定）
evaluate(snap, state=None) -> tuple[list[dict], dict]
    # 纯函数规则判定；state={"hub_lost_streak","sess_stall","prev_sess_ticks"}——批 66b 起均为
    # {account_id: n} dict（per-account，调用方持久化）
    # 规则：R1 unit_down（auto-restart 豁免）/R2 unit_restarted（计数沿，绕过电平状态机）
    #      R3 dep_down / R4 hub_hb_lost（批 66b：期望集(snap["hub_expected"])×在场集(snap["hubs"])
    #      交叉——DB 行是缺席发现唯一正确来源（SCAN 在场测不到缺席）；连续 2 轮；component=
    #      md-hub:{acct}；legacy 过渡豁免——裸键收编（0∈hubs）在场=键切换/回滚过渡态整轮豁免
    #      （防迁移夜假 critical）；期望集缺供=空集地板：在场集非空即过，全空按 "__all__" streak
    #      保守告警）/ R5 task_blind（warning）/ R6 hub_tick_stalled（批 66b per-account：时段内
    #      sess_ticks 零增长≥2 轮，component=md-hub:{acct}）
    #      R8-R10 资源阈值族：mem_high/swap_high/disk_high（逐挂载点，component=disk:{path}）/
    #      cpu_high（批 78，component=cpu）——阈值 snap["thresholds"]（system_config 配置驱动，
    #      cpu 缺省 0.70/0.90）；恢复豁免 res_evidence per-kind（采集缺失≠恢复）
run_check() -> dict            # beat 30s 入口（risk 队列，expires=25）：采集→snap["hub_expected"] 注入
                               # →判定→沿检测→告警/落库→自身心跳；跨轮状态三键值=JSON dict（批 66b：
                               # 坏值/旧标量形态按空 dict 从零起），写回 json.dumps
report_schema_findings(findings) -> None
    # #48 入口路由：verify_schema 纯函数结果 → 告警/health_event（db 层不引告警依赖）
    # expectations_missing 哨兵 → warning"校验被禁用"
```

### 依赖（本模块 import 谁）
stdlib only（模块级）；`redis`/`src.data_platform.db`/`src.alert_notify.notify` 全部**函数内延迟 import**
（守卫：db 层反向只经 report_schema_findings 单点，不构成环）。

## 二、被调（谁 import 本模块）
- `web_api/main.py`：/metrics /readyz /api/health/components /api/health/events + startup 的 schema 校验
- `scheduler/tasks.py`：`health_monitor_check` beat 任务 → run_check
- `scheduler/app.py`：celery 父进程 schema 校验（import 期一次）
- `strategy_runner/main.py`、`md_hub/main.py`：启动 schema 校验
- `/metrics` 消费方（外部）：Zabbix HTTP agent（Phase 2）/ Prometheus / Grafana

## 三、读写表
- **写**：`health_event`（rule_id/component/severity/detail；30 天保留，每日 prune）；
  `system_metric`（每 60s 落一行 mem/swap/disk total+used + cpu_used + 四个 `*_used_avg`——批 78 起，
  avg 列=5 分钟矩形窗 FIR 均值（含当前行最近 5 点，部分窗口用可得行；per-kind 过滤 NULL）；
  大数字/告警判定用真值，avg 仅供 sparkline 曲线）
- **读**：`system_config`（阈值+采集周期配置，60s 缓存）；`system_metric`（FIR 窗口取最近 4 行）；
  `_hub_expected_ids` 读 external_interface（66b）；collector 探活 SELECT 1

## 四、Valkey 键
| 键 | 语义 |
|---|---|
| `quant:hm:health-monitor` | 自身心跳（TTL 120s；外层监测"监控死了"用） |
| `quant:hm:state:{rule}:{component}` | 电平沿状态（TTL 7200；R4/R6 的 component=`md-hub:{account_id}`——批 66b per-account） |
| `quant:hm:nr:{unit}` | NRestarts 上次值（计数沿，TTL 86400） |
| `quant:hm:hub_lost_streak` / `r6_stall` / `r6_prev_sess_ticks` | R4/R6 跨轮证据——**批 66b 起值=JSON dict** `{account_id: n}`（per-account streak；旧标量形态按空 dict 从零起） |

## 五、不变量
1. **通知链独立于存储**：run_check 的通知循环在最外层，Valkey 挂 → 无去重直发（dep_down(valkey) 本身就是最紧急事件）
2. **证据缺失 ≠ 证据健康**：units 采集失败 → 跳过 R1 判定与恢复扫描
3. **计数沿不进电平状态机**（D-F4）：unit_restarted 直发，30s 后不发假"恢复"
4. evaluate 纯函数（可测）；副作用只在 run_check/入口路由
